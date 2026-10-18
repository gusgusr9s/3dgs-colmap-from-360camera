"""Person-mask post-processing: OpenCV is the reference, CUDA reproduces it batch-wise with torch."""
from __future__ import annotations

import cv2
import numpy as np

# NumPy compares a float32 map against float32(0.05); the CUDA path uses the same value.
THRESHOLD = float(np.float32(0.05))
CLOSE_KERNEL, DILATE_KERNEL = 21, 15


def cpu_ignored(probability, size, nadir):
    """Upsample, threshold, add the nadir, close and dilate (the original OpenCV path)."""
    width, height = size
    probability = cv2.resize(probability, (width, height), interpolation=cv2.INTER_LINEAR)
    ignored = (probability > 0.05).astype(np.uint8)
    ignored = np.maximum(ignored, nadir)
    ignored = cv2.morphologyEx(ignored, cv2.MORPH_CLOSE, np.ones((CLOSE_KERNEL, CLOSE_KERNEL), dtype=np.uint8))
    return cv2.dilate(ignored, np.ones((DILATE_KERNEL, DILATE_KERNEL), dtype=np.uint8))


def fill_lower_components(ignored):
    """A close operator or selfie stick is often only partially recognized.
    Fill the hull of every large component that reaches the lower image (in place)."""
    height, width = ignored.shape
    contours, _ = cv2.findContours(ignored, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for contour in contours:
        x, y, box_width, box_height = cv2.boundingRect(contour)
        if cv2.contourArea(contour) > width * height * 0.01 and y + box_height > height * 0.65:
            cv2.fillConvexPoly(ignored, cv2.convexHull(contour), 1)
            cv2.rectangle(ignored, (max(0, x - 12), y), (min(width - 1, x + box_width + 12), height - 1), 1)
    return ignored


def valid_mask(ignored):
    """Brush convention: 255 trains, 0 is ignored. Same values as np.where/np.mean on a 0/1 mask,
    without the int64 temporaries that made this the costliest CPU step."""
    return cv2.compare(ignored, 0, cv2.CMP_EQ), cv2.countNonZero(ignored) / ignored.size


class CudaPostprocess:
    """Batched CUDA version of cpu_ignored.

    OpenCV hands float INTER_LINEAR to IPP, which evaluates each pass as fma(f, b - a, a) with
    double-precision source coordinates. The horizontal pass emulates that exactly (float64);
    the full-size vertical pass runs in float32 and only pixels within 1e-5 of the threshold
    are recomputed with the fused form, so the binary result follows IPP. Morphology is exact."""

    NEAR = 1e-5  # float32 lerp is within ~5e-7 of IPP for values in [0, 1]

    def __init__(self, torch, device):
        self.torch, self.device = torch, device
        self._axes, self._nadirs = {}, {}
        self.verified = False

    def _axis(self, source, target):
        key = (source, target)
        if key not in self._axes:
            coordinate = (np.arange(target, dtype=np.float64) + 0.5) * (source / target) - 0.5
            start = np.floor(coordinate)
            weight = coordinate - start
            weight[start < 0] = 0; start[start < 0] = 0
            weight[start >= source - 1] = 0; start[start >= source - 1] = source - 1
            start = start.astype(np.int64)
            tensor = lambda value: self.torch.from_numpy(value).to(self.device)
            self._axes[key] = (tensor(start), tensor(np.minimum(start + 1, source - 1)),
                               tensor(weight.astype(np.float32)))
        return self._axes[key]

    def _nadir(self, nadir):
        cached = self._nadirs.get(id(nadir))
        if cached is None:
            # Keep the array so its id cannot be reused by another nadir mask.
            cached = self._nadirs[id(nadir)] = (nadir, self.torch.from_numpy(nadir).to(self.device, self.torch.uint8))
        return cached[1]

    @staticmethod
    def _lerp(first, second, weight):
        """Exact float32 fma(weight, second - first, first): the float64 product of two floats is exact."""
        return (weight.double() * (second - first).double() + first.double()).float()

    def _window_max(self, mask, kernel, dim):
        """Centered running max along dim with a zero border: doubling windows, then two overlapping ones."""
        torch = self.torch
        radius = kernel // 2
        mask = torch.nn.functional.pad(mask, (radius, radius) if dim == -1 else (0, 0, radius, radius))
        width = 1
        while width * 2 <= kernel:
            length = mask.shape[dim] - width
            mask = torch.maximum(mask.narrow(dim, 0, length), mask.narrow(dim, width, length)); width *= 2
        length = mask.shape[dim] - (kernel - width)
        return torch.maximum(mask.narrow(dim, 0, length), mask.narrow(dim, kernel - width, length))

    def _dilate(self, mask, kernel):
        # Zero padding ignores the border, like OpenCV's default dilate border for 0/1 masks.
        return self._window_max(self._window_max(mask, kernel, -1), kernel, -2)

    def _erode(self, mask, kernel):
        # OpenCV erodes with a +inf border, so the image edge never erodes inward.
        return 1 - self._dilate(1 - mask, kernel)

    def above_threshold(self, probabilities, size):
        """Bool (B, H, W): upsampled probability > 0.05, decided as IPP would."""
        width, height = size
        x0, x1, x_weight = self._axis(probabilities.shape[-1], width)
        y0, y1, y_weight = self._axis(probabilities.shape[-2], height)
        rows = self._lerp(probabilities[..., x0], probabilities[..., x1], x_weight)
        top, bottom = rows[..., y0, :], rows[..., y1, :]
        upsampled = top + y_weight[:, None] * (bottom - top)
        del top, bottom
        above = upsampled > THRESHOLD
        near = ((upsampled - THRESHOLD).abs() < self.NEAR).nonzero(as_tuple=True)
        del upsampled
        if len(near[0]):
            image, y, x = near
            above[near] = self._lerp(rows[image, y0[y], x], rows[image, y1[y], x], y_weight[y]) > THRESHOLD
        return above

    def ignored(self, probabilities, size, nadirs):
        """probabilities: (B, h, w) tensor on the device; returns a (B, H, W) uint8 0/1 array."""
        torch = self.torch
        with torch.inference_mode():
            above = self.above_threshold(probabilities.to(self.device, torch.float32), size)
            ignored = torch.maximum(above.to(torch.uint8), torch.stack([self._nadir(nadir) for nadir in nadirs]))
            ignored = self._dilate(self._erode(self._dilate(ignored, CLOSE_KERNEL), CLOSE_KERNEL), DILATE_KERNEL)
            return ignored.cpu().numpy()

    def batch(self, probabilities, sizes, nadirs):
        """Per item: the CUDA mask, or None where only the OpenCV path is verified (downscaling)."""
        result = [None] * len(sizes)
        source_height, source_width = probabilities.shape[-2:]
        for size in dict.fromkeys(sizes):
            if size[0] < source_width or size[1] < source_height:
                continue
            index = [i for i, value in enumerate(sizes) if value == size]
            selected = probabilities if len(index) == len(sizes) else probabilities[index]
            masks = self.ignored(selected, size, [nadirs[i] for i in index])
            for position, mask in zip(index, masks):
                result[position] = mask
        return result

    def checked_batch(self, probabilities, sizes, nadirs):
        """batch() plus a one-time comparison of the first batch with OpenCV, so a driver or torch
        change cannot alter masks silently. Returns (CPU probabilities or None, masks)."""
        masks = self.batch(probabilities, sizes, nadirs)
        missing = any(mask is None for mask in masks)
        cpu = probabilities.float().cpu().numpy() if missing or not self.verified else None
        if not self.verified and not all(mask is None for mask in masks):
            differing = sum(int(np.count_nonzero(mask != cpu_ignored(cpu[index], sizes[index], nadirs[index])))
                            for index, mask in enumerate(masks) if mask is not None)
            if differing:
                raise RuntimeError(f"CUDA masks differ from OpenCV on {differing} pixels")
            self.verified = True
        return cpu, masks
