# -*- coding: utf-8 -*-
"""浏览器锁 · browser_lock.py
目的：同一时刻只允许**一个进程**用 chrome_profile 起 Chrome（互斥锁 / Mutex）
      —— 推广、采集、查双百、换源、送修 全都走它，谁抢到谁先用，其余排队。

用法：
    import browser_lock
    with browser_lock.guard("推广批"):
        < 起 Chrome 干活 >

行为：
    · 拿不到锁 → 每 poll 秒重试（阻塞式）
    · 超过 timeout 秒 → 抛 LockTimeout（调用方应捕获并记日志退出，不硬抢）
    · 进程退出 → 锁自动释放（msvcrt 文件锁，不用手动清）
术语：带超时的阻塞式互斥锁（blocking mutex with timeout）
"""
import os, io, time, msvcrt, datetime

BASE = os.path.dirname(os.path.abspath(__file__))
# ★ 0915：锁必须共用同一把——插件目录里的副本也要锁到「推广一键跑\runtime」
#   （否则 base 指向插件目录 → 锁到另一个文件 → 等于没锁，和推广撞车）
_TG = r"C:\Users\jdt-pty\Desktop\推广一键跑"
_RT = os.path.join(_TG, "runtime")
RUNTIME = _RT if os.path.isdir(_RT) else os.path.join(BASE, "runtime")
LOCK = os.path.join(RUNTIME, "chrome_profile.lock")
LOG = os.path.join(RUNTIME, "browser_lock.log")
HOLDER = os.path.join(RUNTIME, "chrome_profile.holder")   # 持有者信息（独立文件——锁文件本身读不到）


class LockTimeout(Exception):
    pass


def _log(msg):
    line = "%s  %s" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        os.makedirs(RUNTIME, exist_ok=True)
        with io.open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    try:
        print(line)
    except Exception:
        pass


def _holder():
    try:
        return io.open(HOLDER, encoding="utf-8").read().strip() or "?"
    except Exception:
        return "?"


class guard(object):
    """with guard('推广批'): ...  —— 带超时的阻塞互斥锁"""

    def __init__(self, who="未命名", timeout=900, poll=5):
        self.who = who
        self.timeout = timeout
        self.poll = poll
        self._fh = None
        self._notified = False

    def __enter__(self):
        os.makedirs(RUNTIME, exist_ok=True)
        t0 = time.time()
        while True:
            fh = None
            try:
                fh = io.open(LOCK, "a+", encoding="utf-8")
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)    # 非阻塞抢锁
                fh.seek(0)
                fh.truncate()
                fh.write("%s | pid=%d | %s" % (self.who, os.getpid(),
                                               datetime.datetime.now().strftime("%H:%M:%S")))
                fh.flush()
                self._fh = fh
                try:
                    io.open(HOLDER, "w", encoding="utf-8").write(
                        "%s | pid=%d | %s" % (self.who, os.getpid(),
                                              datetime.datetime.now().strftime("%H:%M:%S")))
                except Exception:
                    pass
                waited = time.time() - t0
                _log("[%s] 拿到锁 pid=%d%s" % (self.who, os.getpid(),
                                               ("（等了 %.0f 秒）" % waited) if waited > 2 else ""))
                return self
            except OSError:
                try:
                    if fh:
                        fh.close()
                except Exception:
                    pass
                if time.time() - t0 > self.timeout:
                    h = _holder()
                    _log("[%s] 等锁超时（%.0f 秒）——放弃。当前持有：%s" % (self.who, self.timeout, h))
                    raise LockTimeout("浏览器锁超时（持有者：%s）" % h)
                if not self._notified:
                    _log("[%s] 锁被占（%s）——排队等待…" % (self.who, _holder()))
                    self._notified = True
                time.sleep(self.poll)

    def __exit__(self, exc_type, exc, tb):
        try:
            if self._fh:
                self._fh.seek(0)
                self._fh.truncate()
                self._fh.close()
            self._fh = None
            try:
                io.open(HOLDER, "w", encoding="utf-8").write("")
            except Exception:
                pass
            _log("[%s] 释放锁" % self.who)
        except Exception:
            pass
        return False


if __name__ == "__main__":
    import sys
    who = (sys.argv[1] if len(sys.argv) > 1 else "自测")
    sec = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    print("尝试拿锁：", who)
    with guard(who, timeout=30):
        print("拿到，占用 %d 秒…" % sec)
        time.sleep(sec)
    print("已释放")
