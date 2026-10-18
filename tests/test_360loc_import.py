"""Check publisher pose/depth conversion against independent projection math."""
import sys
from pathlib import Path
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from prepare_360loc_model import check_depth_scale, depth_points
from app.pipeline.spherical_to_brush import crop_rotation
from app.pipeline.pano_to_views import perspective_map


class PublishedGeometryTests(unittest.TestCase):
    def test_depth_rays_match_publisher_longitude_latitude(self):
        depth = np.full((100,200), 1000, dtype=np.uint16)
        rays, values, u, v = depth_points(depth, stride=25)
        self.assertTrue(np.allclose(np.linalg.norm(rays,axis=1), 1))
        center = np.flatnonzero((u == 100) & (v == 50))[0]
        np.testing.assert_allclose(rays[center], [0,0,1], atol=1e-12)
        np.testing.assert_allclose(rays[0], [0,-1,0], atol=1e-12)
        self.assertTrue(np.all(values == 1000))

    def test_missing_and_extreme_depth_is_excluded(self):
        depth = np.zeros((100,200), dtype=np.uint16)
        depth[25,100], depth[25,125], depth[25,150] = 1000, 6000, 20
        rays, values, u, v = depth_points(depth, 25)
        self.assertEqual(values.tolist(), [1000])
        self.assertEqual(u.tolist(), [100])

    def test_crop_pose_matches_source_panorama_projection(self):
        width, height = 65,65
        x, y = np.meshgrid(np.arange(width),np.arange(height))
        focal = width / 2
        camera_rays = np.stack([(x-32)/focal, (y-32)/focal, np.ones_like(x)],-1)
        camera_rays = camera_rays / np.linalg.norm(camera_rays,axis=-1,keepdims=True)
        for yaw in (0,45,135,270):
            for pitch in (-55,0,55):
                # Brush axes are Y-up/Z-back; multiplying its pose recovers
                # the author's COLMAP ray after converting the local axes.
                brush_rotation = crop_rotation(yaw,pitch).copy()
                brush_rotation[:,1:3] *= -1
                brush_rays = camera_rays.copy(); brush_rays[...,1:3] *= -1
                panorama_rays = brush_rays @ brush_rotation.T
                mx,my,_ = perspective_map(width,height,90,yaw,pitch)
                lon = (mx/(width-1)-.5)*2*np.pi
                lat = (.5-my/(height-1))*np.pi
                expected = np.stack([np.cos(lat)*np.sin(lon),-np.sin(lat),np.cos(lat)*np.cos(lon)],-1)
                np.testing.assert_allclose(panorama_rays,expected,atol=1e-5)

    def test_depth_scale_recovers_known_metric_plane(self):
        height,width = 384,768
        u,v = np.meshgrid(np.arange(width),np.arange(height))
        lon = u*2*np.pi/width-np.pi
        lat = np.pi/2-v*np.pi/height
        rz = np.cos(lat)*np.cos(lon)
        depth = np.zeros((height,width),dtype=np.uint16)
        good = rz > .22
        depth[good] = np.rint(1000/rz[good]).astype(np.uint16)
        a,b = np.eye(4),np.eye(4)
        b[2,3] = 1.5
        depth_b = np.zeros_like(depth)
        depth_b[good] = np.rint(850/rz[good]).astype(np.uint16)
        scores = check_depth_scale(depth,depth_b,a,b)
        self.assertGreater(scores['0.01']['within_3_percent'], .9)
        self.assertLess(scores['0.01']['median_abs_log_error'], .01)


if __name__ == '__main__':
    unittest.main()
