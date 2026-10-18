"""Read measured runs and compare them with explicitly labelled planning estimates."""
from __future__ import annotations

import html
import json
import math
from pathlib import Path
import re
from urllib.parse import quote


# Representative runs: two short smoke tests and two complete passes of one room.
RUNS = (
    ("832dfb8b31", "20261013_203220_964927f8", "DJI 8K · High 1024"),
    ("3b74c7f7c0", "20261013_215442_e4691045", "DJI 8K · Ultra 1536"),
    ("a147ed6bb2", "20261008_012920_c9975336", "Sample 360 · ช่วง 2 วินาที"),
    ("bb41eed643", "20261008_013616_352f99d7", "Insta360 X4 · ช่วง 4 วินาที"),
)
AI_GPU_RANGES = (
    ("RTX 3060", "12 GB", "35–90", "60–180"),
    ("RTX 3090", "24 GB", "20–45", "35–120"),
    ("RTX 4090", "24 GB", "10–25", "25–100"),
    ("RTX 5090", "32 GB", "7–20", "15–90"),
    ("NVIDIA L4", "24 GB", "25–60", "45–150"),
    ("A100 80GB", "80 GB", "10–30", "25–100"),
    ("H100 80GB", "80 GB", "6–20", "20–90"),
    ("H200 141GB", "141 GB", "6–18", "20–90"),
)
SOURCES = (
    ("gsplat: RTX 4090 / Splatfacto 30K = 13:30, ต่าง dataset และ trainer",
     "https://docs.gsplat.studio/versions/0.1.11/tests/eval.html"),
    ("gsplat: TITAN RTX / 30K = 35:49, ต่าง dataset และ trainer",
     "https://docs.gsplat.studio/main/tests/eval.html"),
    ("Insta360: Spatial Capture และโควตา 4 ครั้งต่อเดือน (ตรวจ 13 ต.ค. 2026)",
     "https://www.insta360.com/us/product/insta360-x6"),
)
PHASE_LABELS = (
    ("preparation", "เตรียม / spherical keyframes"), ("features", "COLMAP features"),
    ("matching", "COLMAP matching"), ("poses", "COLMAP camera poses"),
    ("dataset", "เตรียม dataset"), ("masks", "Person masks · CPU"),
    ("training", "ช่วง Training ของแอป"), ("finalize", "ตรวจ / Export"),
)


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def measured_run(jobs, job, run, label):
    directory = jobs / job / "model_runs" / run
    status = read_json(directory / "status.json")
    quality = status.get("quality", {})
    if status.get("state") != "done" or status.get("refined_from") or "6700 XT" not in quality.get("compute_device", ""):
        return None
    view_run = status.get("view_run_id", "")
    # Read the exact view generation referenced by this model, never the latest job status.
    if not re.fullmatch(r"\d{8}_\d{6}_[a-f0-9]{8}", view_run):
        return None
    views = read_json(jobs / job / "view_runs" / view_run / "status.json")
    if views.get("state") != "ready" or views.get("elapsed_seconds") is None:
        return None
    try:
        log = (directory / "3d.log").read_text(encoding="utf-8", errors="replace")
    except OSError:
        log = ""
    times = re.findall(r"Done training! Took FormattedDuration\((\d+)s\)", log)
    train_seconds = float(times[-1]) if times else None
    model_seconds = float(status["elapsed_seconds"])
    views_seconds = float(views["elapsed_seconds"])
    plan = views.get("capture_plan", {})
    settings = status.get("settings", {})
    source = status.get("source_profile", {})
    return dict(
        job=job, run_id=run, label=label, source=source.get("name"),
        source_fingerprint=source.get("fingerprint"),
        source_resolution=f'{source.get("width", "?")}×{source.get("height", "?")}',
        duration_seconds=plan.get("end_seconds", 0) - plan.get("start_seconds", 0),
        positions=plan.get("positions"), views=plan.get("total_views"),
        width=settings.get("width"), height=settings.get("height"),
        steps=quality.get("training_steps"), train_seconds=train_seconds,
        views_seconds=views_seconds, model_seconds=model_seconds,
        total_seconds=views_seconds + model_seconds,
        other_seconds=model_seconds - train_seconds if train_seconds is not None else None,
        phase_durations_seconds=status.get("phase_durations_seconds", {}),
        auto_model=views.get("auto_model") is True, backend=quality.get("compute_backend"),
        colmap_backend=quality.get("colmap_backend", "ไม่ได้บันทึก"),
        mask_device=quality.get("mask_device", "ไม่ได้บันทึก"),
        psnr_db=quality.get("eval_psnr_mean_db"), splats=quality.get("published_splats"),
        model= status.get("model"),
        evidence=f"web_data/jobs/{job}/model_runs/{run}/status.json",
        log_evidence=f"web_data/jobs/{job}/model_runs/{run}/3d.log",
        view_evidence=f"web_data/jobs/{job}/view_runs/{view_run}/status.json",
    )


def predict(baseline, *, positions, views, width, height, steps):
    """A deliberately simple, inspectable extrapolation; no fitting to the target run."""
    values = [positions, views, width, height, steps]
    if any(not math.isfinite(float(v)) or float(v) <= 0 for v in values):
        raise ValueError("ค่าทุกช่องต้องเป็นจำนวนบวก")
    if baseline.get("train_seconds") is None:
        raise ValueError("ไม่มีเวลา train จาก log สำหรับ baseline")
    pixels = width * height / (baseline["width"] * baseline["height"])
    view_time = baseline["views_seconds"] * views / baseline["views"] * pixels
    train_time = baseline["train_seconds"] * steps / baseline["steps"] * pixels
    other = baseline["other_seconds"] * positions / baseline["positions"]
    return dict(views_seconds=view_time, train_seconds=train_time, other_seconds=other,
                total_seconds=view_time + train_time + other)


def build_report(jobs):
    rows = [result for args in RUNS if (result := measured_run(Path(jobs), *args))]
    baseline = next((r for r in rows if r["job"] == RUNS[0][0]), None)
    target = next((r for r in rows if r["job"] == RUNS[1][0]), None)
    validation = None
    if (baseline and target and baseline["train_seconds"] is not None
            and baseline["source_fingerprint"]
            and baseline["source_fingerprint"] == target["source_fingerprint"]):
        estimated = predict(baseline, **{k: target[k] for k in ("positions", "views", "width", "height", "steps")})
        validation = dict(estimated=estimated, actual=target,
                          error_percent=100 * (estimated["total_seconds"] / target["total_seconds"] - 1))
    return dict(measured=rows, baseline=baseline, validation=validation,
                ai_gpu_ranges=AI_GPU_RANGES, sources=SOURCES,
                cloud=dict(reference_minutes=90, range_minutes=[60, 120], measured_same_scene=False,
                           provenance="ช่วงอ้างอิงจากสรุป AI ที่ผู้ใช้แนบ; ยังไม่ยืนยันเวลา 90 นาทีจากคู่มือทางการ"))


def duration(seconds):
    if seconds is None:
        return "—"
    minutes, secs = divmod(round(seconds), 60)
    return f"{minutes}:{secs:02d}"


def markdown_report(report):
    lines = ["# RX 6700 XT เทียบค่าประมาณ GPU และ Insta360 Cloud", "",
             "ข้อมูลผลจริงจาก run status และเวลา `Done training!` ของ Brush; ตรวจ 13 ต.ค. 2026",
             "", "## ผลจริง (เวลา นาที:วินาที)", "",
             "| งาน | ช่วงคลิป (วินาที) | ตำแหน่ง / Views | ขนาด | Steps | Train | Views | Model รวมเตรียมข้อมูล | ทั้งงาน | Masked PSNR |",
             "| --- | ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for r in report["measured"]:
        lines.append(f'| {r["label"]} | {r["duration_seconds"]:.2f} | {r["positions"]} / {r["views"]:,} | {r["width"]}×{r["height"]} | {r["steps"]:,} | {duration(r["train_seconds"])} | {duration(r["views_seconds"])} | {duration(r["model_seconds"])} | {duration(r["total_seconds"])} | {r["psnr_db"]} dB |')
    ultra = next((r for r in report["measured"] if r["job"] == RUNS[1][0]), None)
    if ultra and ultra["phase_durations_seconds"]:
        lines += ["", "### เวลารายขั้นของ Ultra 1536", "", "| ขั้น | เวลา นาที:วินาที |", "| --- | ---: |",
                  f'| สร้าง Views | {duration(ultra["views_seconds"])} |']
        lines += [f'| {label} | {duration(ultra["phase_durations_seconds"].get(key))} |' for key, label in PHASE_LABELS]
        lines += ["", "ช่วง Training ของแอปรวม startup/evaluation รอบ trainer จึงต่างจากเวลาที่ Brush รายงานใน `Done training!`; ไม่บวกสองค่านี้ซ้ำ",
                  "รอบที่วัดข้างต้น COLMAP และ person masks ใช้ CPU ส่วน Brush ใช้ RX 6700 XT / Vulkan จึงเปลี่ยน GPU อย่างเดียวไม่ลดทุกขั้นในสัดส่วนเดียวกัน",
                  "รอบ baseline เหล่านี้สร้างก่อนเพิ่ม WGPU Views / DirectML masks ดูผลเร่งเตรียมข้อมูลเพิ่มเติมใน GPU_PREPARATION.md"]
    lines += ["", "ทั้งงาน = Views + Model; Model รวม COLMAP, dataset, masks, Brush, evaluation และ export แล้ว จึงห้ามบวก Train ซ้ำ",
              "เวลา local เริ่มจากไฟล์ที่อยู่ในเครื่องแล้ว ไม่รวมถ่าย, stitch/export MP4, อัปโหลด, ติดตั้ง และเปิด viewer",
              "", "## ตารางจากสรุป AI", "",
              "สมมติ 8K 120 วินาที, 300–600 ภาพที่ extract, 30K steps, COLMAP + gsplat/Nerfstudio. ทุกแถว GPU ด้านล่างเป็นค่าประมาณจาก AI ไม่ใช่ผลทดสอบไฟล์เรา",
              "", "| GPU | VRAM | Train 30K (นาที) | ทั้งงาน (นาที) | ที่มา |", "| --- | --- | --- | --- | --- |"]
    for gpu, vram, train, total in AI_GPU_RANGES:
        lines.append(f"| {gpu} | {vram} | {train} | {total} | ประมาณจาก AI |")
    for r in report["measured"][:2]:
        lines.append(f'| RX 6700 XT · {r["label"]} | 12 GB | {duration(r["train_seconds"])} ({r["steps"]:,} steps) | {duration(r["total_seconds"])} | วัดจริง Brush/Vulkan; input ต่างจากสมมติฐาน AI |')
    lines += ["| Insta360 Cloud | ไม่เปิดเผย | ไม่เปิดเผย | 60–120 (อ้างอิง 90) | จากสรุป AI; ไม่มีผลวัดฉากเดียวกัน |",
              "", "AI เดิมไม่มีค่าของ RX 6700 XT และไม่ให้สัมประสิทธิ์สูตรครบ จึงคำนวณ % error ของ AI เดิมสำหรับ RX ไม่ได้ และผลจริงนี้ไม่ยืนยันความเร็ว RTX 5090",
              "", "## เครื่องคำนวณจาก baseline จริง", "",
              "ใช้ DJI High 1024 เป็น baseline ของ RX 6700 XT / Brush; สูตรใหม่ที่ระบุชัด ไม่ใช่การสร้างสูตร AI เดิมกลับมา",
              "", "- Views = เวลา Views baseline × อัตราจำนวน Views × อัตราจำนวนพิกเซล",
              "- Train = เวลา Train baseline × อัตรา steps × อัตราจำนวนพิกเซล",
              "- งานอื่น = (Model baseline − Train baseline) × อัตราจำนวนตำแหน่ง",
              "", "ไม่ได้สมมติว่า GPU ทุกรุ่นเร็วตาม TFLOPS; งานอื่นรวม CPU pose/masks, startup, eval และ export",
              "สูตรยังไม่คิด scene complexity, splat count, VRAM, nonlinear matching หรือผลของ resolution ต่อ masks",
              "ค่า baseline ย่อมตรง baseline เอง จึงใช้รอบ Ultra เป็นตัวตรวจสูตรอีกหนึ่งรอบโดยไม่ปรับ coefficient ให้เข้ากับ Ultra"]
    v = report["validation"]
    if v:
        lines += ["", "| ขั้น | คำนวณ High → Ultra | Ultra วัดจริง |", "| --- | ---: | ---: |"]
        for label, key in [("Views", "views_seconds"), ("Train", "train_seconds"), ("งานอื่น", "other_seconds"), ("ทั้งงาน", "total_seconds")]:
            lines.append(f'| {label} | {duration(v["estimated"][key])} | {duration(v["actual"][key])} |')
        lines += ["", f'รวมคลาดเคลื่อน {v["error_percent"]:+.2f}% (ค่าคำนวณ − ค่าจริง เทียบค่าจริง). ใกล้กันสำหรับห้องนี้หนึ่งคู่เท่านั้น; error รายขั้นหักล้างกัน จึงยังรับรองความแม่นในฉากอื่นไม่ได้',
                  "", "## เวลา Cloud เทียบผลจริง", "",
                  "90 นาทีเป็นเวลาอ้างอิงจากสรุปที่แนบ ไม่ใช่เวลา cloud ที่เราวัด และยังไม่ยืนยันว่ารวม upload/queue เท่าใด"]
        for r in report["measured"][:2]:
            lines.append(f'- {r["label"]}: {duration(r["total_seconds"])} เทียบ 90 นาที = อัตราส่วน {5400/r["total_seconds"]:.2f} เท่า; เทียบช่วง 60–120 นาที = {3600/r["total_seconds"]:.2f}–{7200/r["total_seconds"]:.2f} เท่า (ต่าง input/trainer/ขอบเขตเวลา)')
    lines += ["", "## ระบบ auto เทียบเท่า Insta360 แล้วหรือยัง?", "",
              "เทียบเคียงด้านขั้นตอนหลัก: stitched panorama MP4 → Views → spherical COLMAP poses → masks/seed checks → Brush GPU → evaluation/export PLY → local web viewer ต่ออัตโนมัติ โดย runs ที่เลือกเปิด auto_model=true",
              "ยังยืนยันว่าเทียบเท่าผลิตภัณฑ์หรือคุณภาพไม่ได้: ไม่มี same-scene cloud A/B, คุณภาพ novel views/กระจกยังต้องตรวจ และ local viewer ไม่ใช่บริการ cloud upload/queue/public share ของ Insta360",
              "raw .insv ยังต้อง stitch/export เป็น MP4 ตาม workflow นี้; ผลรอบ 1024 มี review ภาพ แต่ PSNR ของสองรอบใช้ชุด eval/masks ต่างกัน จึงสรุปว่าคุณภาพดีขึ้น 1.02 dB แบบเทียบตรงไม่ได้",
              "", "## หลักฐานและแหล่งอ้างอิง", ""]
    for r in report["measured"]:
        lines += [f'- {r["label"]}: [{r["evidence"]}]({r["evidence"]}), [Views]({r["view_evidence"]}), [Brush log]({r["log_evidence"]})']
    lines += [f"- [{title}]({url})" for title, url in SOURCES]
    return "\n".join(lines) + "\n"


STYLE = """<style>
.benchmark-wrap{max-width:1200px}.benchmark-wrap h2{margin-top:28px}.benchmark-wrap p{line-height:1.7}
.benchmark-scroll{overflow:auto;margin:16px 0}.benchmark-wrap table{border-collapse:collapse;width:100%;font-size:14px}
.benchmark-wrap th,.benchmark-wrap td{padding:12px;border-bottom:1px solid #cbd5e1;text-align:left;white-space:nowrap}
.benchmark-wrap th{background:#e2e8f0;color:#172033}.benchmark-wrap .measured{background:#e0f2fe;color:#172033}
.benchmark-form{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:14px}
.benchmark-form label{display:grid;gap:6px}.benchmark-form input{width:100%;box-sizing:border-box;padding:10px}
.benchmark-result{background:#e0f2fe;color:#172033;padding:18px;border-radius:12px;margin-top:18px;line-height:1.8}
.benchmark-error{color:#b91c1c}.benchmark-wrap small{white-space:normal;display:block;max-width:400px}
</style>"""


def render_report(report, query=None):
    query = query or {}
    esc = html.escape
    rows = []
    for r in report["measured"]:
        link = f'/jobs/{quote(r["job"])}/model_runs/{quote(r["run_id"])}/status.json'
        rows.append(f'<tr class="measured"><td><a href="{link}">{esc(r["label"])}</a><small>{esc(r["source_resolution"])} · {r["duration_seconds"]:.2f} s · {r["positions"]} poses / {r["views"]:,} views</small></td><td>{r["width"]}×{r["height"]}<small>{r["steps"]:,} steps · Brush/{esc(r["backend"] or "?")}</small></td><td>{duration(r["train_seconds"])}</td><td>{duration(r["views_seconds"])}</td><td>{duration(r["model_seconds"])}</td><td><strong>{duration(r["total_seconds"])}</strong></td><td>{r["psnr_db"]} dB</td></tr>')
    content = STYLE + '<div class="benchmark-wrap"><p class="muted">รอบ baseline เหล่านี้สร้างก่อนเพิ่ม GPU สำหรับ Views/masks ตัวเลขเตรียมข้อมูลจึงยังอ้างอิง CPU ของรอบเดิม</p><p>ผลจริง RX 6700 XT เทียบกับค่าประมาณในสรุป AI และเวลาอ้างอิง Insta360 Cloud</p><h2>ผลจริงจากเครื่องเรา · นาที:วินาที</h2>'
    content += '<div class="benchmark-scroll"><table><thead><tr><th>งาน / Input</th><th>การเทรน</th><th>Train</th><th>Views</th><th>Model รวมเตรียมข้อมูล</th><th>ทั้งงาน</th><th>Masked PSNR</th></tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div>'
    content += '<p>ทั้งงาน = Views + Model; Train อยู่ใน Model แล้ว ไม่บวกซ้ำ เวลาเริ่มจาก stitched MP4 ในเครื่อง ไม่รวมถ่าย, stitch, upload, ติดตั้ง หรือเปิด viewer. PSNR ต่างชุด eval/masks จึงใช้เทียบคุณภาพสองรอบโดยตรงไม่ได้</p>'
    ultra = next((r for r in report["measured"] if r["job"] == RUNS[1][0]), None)
    if ultra and ultra["phase_durations_seconds"]:
        content += '<details><summary>เวลารายขั้น Ultra · COLMAP และ masks ใช้ CPU</summary><div class="benchmark-scroll"><table><tr><th>ขั้น</th><th>นาที:วินาที</th></tr>'
        content += f'<tr><td>สร้าง Views</td><td>{duration(ultra["views_seconds"])}</td></tr>'
        content += ''.join(f'<tr><td>{label}</td><td>{duration(ultra["phase_durations_seconds"].get(key))}</td></tr>' for key, label in PHASE_LABELS)
        content += '</table></div><p>ช่วง Training ของแอปรวม startup/evaluation จึงต่างจากเวลา Done training! ที่ Brush รายงาน ไม่บวกสองค่านี้ซ้ำ และเปลี่ยน GPU อย่างเดียวไม่ได้ลดเวลาของ CPU ทุกขั้น</p></details>'
    gpu_rows = ''.join(f'<tr><td>{gpu}</td><td>{vram}</td><td>{train}</td><td>{total}</td><td>ประมาณจาก AI</td></tr>' for gpu, vram, train, total in AI_GPU_RANGES)
    for r in report["measured"][:2]:
        gpu_rows += f'<tr class="measured"><td>RX 6700 XT · {esc(r["label"])}</td><td>12 GB</td><td>{duration(r["train_seconds"])} ({r["steps"]:,} steps)</td><td>{duration(r["total_seconds"])}</td><td>วัดจริง · Input/Trainer ต่างจาก AI</td></tr>'
    content += '<h2>ตาราง GPU ตามสรุป AI + RX วัดจริง</h2><p>แถว AI สมมติ 8K 120 วินาที, extract 300–600 ภาพ, 30K steps, gsplat/Nerfstudio. ตารางนี้ยังไม่ใช่ benchmark ไฟล์เดียวกันบนทุก GPU</p><div class="benchmark-scroll"><table><tr><th>GPU</th><th>VRAM</th><th>Train (นาที)</th><th>ทั้งงาน (นาที)</th><th>ที่มา</th></tr>' + gpu_rows + '<tr><td>Insta360 Cloud</td><td>ไม่เปิดเผย</td><td>ไม่เปิดเผย</td><td>60–120 (อ้างอิง 90)</td><td>จากสรุป AI · ไม่มีผลวัดฉากเดียวกัน</td></tr></table></div><p>AI เดิมไม่มีค่า RX และสัมประสิทธิ์สูตรครบ จึงยังคำนวณ % error ของ AI สำหรับ RX ไม่ได้ และไม่สามารถอนุมานความเร็ว RTX 5090 จากผล Brush บน AMD นี้</p>'
    baseline = report["baseline"]
    v = report["validation"]
    if v:
        content += '<h2>ลองตรวจสูตรใหม่: High → Ultra ของห้องเดียวกัน</h2><p>ใช้เวลา High 1024 เป็น baseline โดยไม่ปรับ coefficient ให้เข้ากับ Ultra. Views แปรตามจำนวนภาพ × พิกเซล; Train แปรตาม steps × พิกเซล; งานอื่นแปรตามจำนวนตำแหน่ง</p><div class="benchmark-scroll"><table><tr><th>ขั้น</th><th>คำนวณ</th><th>วัดจริง Ultra</th></tr>'
        for label, key in [("Views", "views_seconds"), ("Train", "train_seconds"), ("งานอื่น", "other_seconds"), ("ทั้งงาน", "total_seconds")]:
            content += f'<tr><td>{label}</td><td>{duration(v["estimated"][key])}</td><td>{duration(v["actual"][key])}</td></tr>'
        content += f'</table></div><p>รวมคลาดเคลื่อน {v["error_percent"]:+.2f}% ใกล้กันสำหรับหนึ่งคู่นี้ แต่ error รายขั้นหักล้างกัน จึงยังรับรองสูตรในฉากอื่นไม่ได้</p>'
    if baseline and baseline["train_seconds"] is not None:
        defaults = dict(positions=488, views_per_position=16, resolution=1536, steps=30000, cloud_minutes=90)
        values = {k: str(query.get(k, [str(d)])[0]) for k, d in defaults.items()}
        content += '<h2>เครื่องคำนวณ RX 6700 XT / Brush</h2><p>Baseline: DJI High 1024. ไม่คิด scene complexity, splat count, VRAM หรือการ scale ของ CPU masks ตาม resolution</p><form class="benchmark-form" action="/benchmarks" method="get">'
        for key, label in [("positions", "ตำแหน่งกล้อง"), ("views_per_position", "Views ต่อตำแหน่ง"), ("resolution", "ขนาดภาพสี่เหลี่ยม (px)"), ("steps", "Training steps"), ("cloud_minutes", "Cloud อ้างอิง (นาที)")]:
            content += f'<label>{label}<input type="number" name="{key}" value="{esc(values[key], quote=True)}" min="1" step="1" required></label>'
        content += '<button type="submit" class="primary">คำนวณเทียบ</button></form>'
        try:
            numbers = {k: int(value) for k, value in values.items()}
            if any(value <= 0 or value > 1000000 for value in numbers.values()):
                raise ValueError("กรอกจำนวนเต็ม 1–1,000,000")
            estimated = predict(baseline, positions=numbers["positions"], views=numbers["positions"] * numbers["views_per_position"], width=numbers["resolution"], height=numbers["resolution"], steps=numbers["steps"])
            content += f'<div class="benchmark-result" role="status">คำนวณทั้งงาน <strong>{duration(estimated["total_seconds"])}</strong> · Train {duration(estimated["train_seconds"])} · Views {duration(estimated["views_seconds"])} · งานอื่น {duration(estimated["other_seconds"])}<br>Cloud อ้างอิง {numbers["cloud_minutes"]} นาที ÷ Local คำนวณ = {numbers["cloud_minutes"] * 60 / estimated["total_seconds"]:.2f} เท่า · เป็นอัตราส่วนเวลาอ้างอิง</div>'
        except (ValueError, OverflowError):
            content += '<p class="benchmark-error" role="alert">กรอกจำนวนเต็ม 1–1,000,000 ทุกช่อง</p>'
    else:
        content += '<p>ยังไม่มี baseline พร้อม log ใน workspace นี้ เครื่องคำนวณจะแสดงเมื่อมีผล High 1024 ที่อ้างอิง</p>'
    content += '<h2>เทียบ Cloud และระบบ auto</h2><p>Cloud 60–120 นาที / จุดอ้างอิง 90 นาทีมาจากสรุป AI ที่แนบ ยังไม่ยืนยันตัวเลข 90 นาทีจากคู่มือทางการ และไม่มีผลวัด Cloud ของห้องนี้</p>'
    for r in report["measured"][:2]:
        content += f'<p>{esc(r["label"])} วัดจริง {duration(r["total_seconds"])} เทียบ 90 นาที = {5400 / r["total_seconds"]:.2f} เท่า; เทียบช่วง 60–120 = {3600/r["total_seconds"]:.2f}–{7200/r["total_seconds"]:.2f} เท่า · Input/Trainer/ขอบเขตเวลาต่างกัน</p>'
    content += '<p><strong>เทียบเคียงด้าน auto pipeline หลักแล้ว:</strong> stitched MP4 → Views → spherical COLMAP → masks/seed checks → Brush GPU → evaluation/export PLY → local viewer. Runs ที่เลือกใช้ auto_model=true</p><p><strong>ยังยืนยันความเทียบเท่าด้านคุณภาพและผลิตภัณฑ์ไม่ได้:</strong> ต้องมี same-scene Cloud A/B และตรวจ novel views/กระจกเพิ่มเติม; raw .insv ต้อง stitch/export ก่อน และบริการ Cloud upload/queue/public share ยังเป็นคนละขอบเขตกับ local app</p><h2>แหล่งอ้างอิง</h2><ul>'
    content += ''.join(f'<li><a href="{esc(url, quote=True)}">{esc(title)}</a></li>' for title, url in SOURCES)
    return content + '</ul></div>'
