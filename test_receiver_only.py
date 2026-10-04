import os
import select
import signal
import socket
import subprocess
import sys
import time
import unittest
import pty
from pathlib import Path


ROOT = Path(__file__).resolve().parent


class ReceiverOnlyTest(unittest.TestCase):
    def test_watch_interrupt_returns_to_console(self):
        master, slave = pty.openpty()
        env = os.environ.copy()
        env.update(DMA_UDP_LISTEN_HOST="127.0.0.1", DMA_BIND_PORT="0")
        process = subprocess.Popen([sys.executable, '-u', str(ROOT / 'main.py')],
                                   cwd=ROOT, env=env, stdin=slave, stdout=slave,
                                   stderr=slave, start_new_session=True)
        os.close(slave)
        def expect(needle):
            data = b''
            deadline = time.monotonic() + 5
            while needle not in data and time.monotonic() < deadline:
                if select.select([master], [], [], .1)[0]:
                    data += os.read(master, 65536)
            self.assertIn(needle, data)
        try:
            expect(b'>>')
            os.write(master, b'watch\n')
            expect(b'Peer: offline')
            process.send_signal(signal.SIGINT)
            expect(b'>>')
            os.write(master, b'status\n')
            expect(b'Peer: offline')
            os.write(master, b'exit\n')
            self.assertEqual(process.wait(timeout=5), 0)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            os.close(master)

    def test_console_available_offline_and_eof_exits(self):
        env = os.environ.copy()
        env.update(DMA_UDP_LISTEN_HOST="127.0.0.1", DMA_BIND_PORT="0")
        for commands in ("status\nexit\n", ""):
            result = subprocess.run(
                [sys.executable, "-u", str(ROOT / "main.py")],
                cwd=ROOT, env=env, input=commands, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn('Console ready', result.stdout)
            if commands:
                self.assertIn('Peer: offline', result.stdout)
                self.assertIn('No main status received', result.stdout)

    def test_receives_without_stdin_and_stops_on_sigterm(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]

        env = os.environ.copy()
        env.update(DMA_UDP_LISTEN_HOST="127.0.0.1", DMA_BIND_PORT=str(port))
        process = subprocess.Popen(
            [sys.executable, "-u", str(ROOT / "main.py"), "--receiver-only"],
            cwd=ROOT,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                ready, _, _ = select.select([process.stdout], [], [], 0.2)
                if ready and "UDP receiver ready" in process.stdout.readline():
                    break
                self.assertIsNone(process.poll(), "receiver exited before startup")
            else:
                self.fail("receiver did not start")

            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                sender.sendto(b"\x01receiver-test", ("127.0.0.1", port))
            process.send_signal(signal.SIGTERM)
            self.assertEqual(process.wait(timeout=5), 0)
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                probe.bind(("127.0.0.1", port))
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            process.stdout.close()


if __name__ == "__main__":
    unittest.main()
