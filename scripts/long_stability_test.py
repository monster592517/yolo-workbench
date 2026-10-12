# -*- coding: utf-8 -*-
"""长稳压力测试: 连续检测 ~95s (2轮), 监控内存/线程/崩溃; 生成测试视频"""
import sys, time, threading, gc
from pathlib import Path
sys.path.insert(0, 'src')
import numpy as np
import cv2
import psutil

PROC = psutil.Process()
VIDEO = Path('runs/stress_test.mp4')
FRAMES, W, H, FPS = 900, 640, 480, 30

def make_video():
    if VIDEO.exists() and VIDEO.stat().st_size > 100000:
        return
    VIDEO.parent.mkdir(parents=True, exist_ok=True)
    wr = cv2.VideoWriter(str(VIDEO), cv2.VideoWriter_fourcc(*'mp4v'), FPS, (W, H))
    for i in range(FRAMES):
        frame = np.zeros((H, W, 3), dtype=np.uint8)
        x = int((i * 4) % (W - 120))
        y = int(80 + 60 * np.sin(i / 20))
        cv2.circle(frame, (x, y), 40, (0, 0, 255), -1)
        cv2.rectangle(frame, (W - x - 100, H - y - 80), (W - x, H - y), (0, 255, 0), -1)
        wr.write(frame)
    wr.release()
    print('video ready:', VIDEO, VIDEO.stat().st_size // 1024, 'KB')

def mem_mb():
    return PROC.memory_info().rss / 1048576

def main():
    make_video()
    from core.detector import DetectionPipeline
    from infrastructure.resource_manager import get_resource_manager
    from infrastructure.config import SourceType

    model = str(Path(__file__).resolve().parent.parent / 'models' / 'yolo11n.pt')
    if not Path(model).exists():
        model = str(Path(__file__).resolve().parent.parent / 'yolo11n.pt')
    results = {'frames': 0, 'errors': 0}
    def on_frame(ann, fps):
        results['frames'] += 1

    start = time.time()
    samples = []
    cycles = 0
    while time.time() - start < 95:
        pipe = DetectionPipeline(model, conf=0.5, device='cpu')
        pipe.set_source(SourceType.VIDEO, str(VIDEO))
        pipe.set_callbacks(on_frame=on_frame)
        pipe.start()
        cycles += 1
        t0 = time.time()
        while time.time() - t0 < 50 and pipe.is_running and time.time() - start < 95:
            time.sleep(5)
            samples.append((round(time.time() - start, 1), round(mem_mb(), 1), threading.active_count(), pipe.is_running))
            gc.collect()
        pipe.stop()
        time.sleep(0.5)

    mems = [s[1] for s in samples]
    threads = [s[2] for s in samples]
    print(f'cycles={cycles} total_frames_cb={results["frames"]} duration={time.time()-start:.1f}s')
    print(f'memory MB: start={samples[0][1] if samples else 0} min={min(mems) if mems else 0} max={max(mems) if mems else 0} last={mems[-1] if mems else 0}')
    print(f'threads: min={min(threads) if threads else 0} max={max(threads) if threads else 0}')
    growth = (mems[-1] - mems[0]) if len(mems) > 1 else 0
    print(f'memory growth: {growth:.1f} MB over {len(samples)} samples')
    get_resource_manager().release_all()
    ok = results['frames'] > 500 and growth < 150
    print('STRESS RESULT:', 'PASS' if ok else 'FAIL')
    sys.exit(0 if ok else 1)

if __name__ == '__main__':
    main()
