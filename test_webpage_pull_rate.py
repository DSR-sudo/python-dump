"""60Hz 拉取前端契约测试。

用 node 运行 test_webpage_pull_rate.js：以最小 DOM/定时器桩执行 web/webpage.html 的
内联脚本，断言轮询间隔 16ms、请求携带 X-Data-Token、服务端 204 时不重复渲染、
数据变化后重新渲染，并保留长时间无变化时的 X-Data-No-Cache 兜底。
node 不可用时跳过（该测试只校验网页交互，不参与设备端运行）。
"""

import os
import shutil
import subprocess
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
HARNESS = os.path.join(HERE, "test_webpage_pull_rate.js")
PAGE = os.path.join(HERE, "web", "webpage.html")


def _find_node():
    for candidate in ("node", "nodejs", os.path.expanduser("~/.local/bin/node")):
        found = shutil.which(candidate)
        if found:
            return found
    return None


class WebpagePullRateTest(unittest.TestCase):
    def test_poll_rate_and_conditional_requests(self):
        if not os.path.isfile(PAGE):
            self.skipTest("web/webpage.html 不存在")
        node = _find_node()
        if not node:
            self.skipTest("node 不可用")
        proc = subprocess.run([node, HARNESS, PAGE], capture_output=True, text=True)
        print(proc.stdout.rstrip())
        self.assertEqual(proc.returncode, 0, "\n" + proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
