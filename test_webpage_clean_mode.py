"""纯净模式前端冒烟测试。

用 node 运行 test_webpage_clean_mode.js：以最小 DOM 桩执行 web/webpage.html 的内联脚本，
断言纯净模式开启后只保留存活玩家、隐藏物资与人机，血量为零的角色整体隐藏(血环、名字武器、尸体)，关闭后恢复原状。
node 不可用时跳过（该测试只校验网页交互，不参与设备端运行）。
"""

import os
import shutil
import subprocess
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
HARNESS = os.path.join(HERE, "test_webpage_clean_mode.js")
PAGE = os.path.join(HERE, "web", "webpage.html")


def _find_node():
    for candidate in ("node", "nodejs", os.path.expanduser("~/.local/bin/node")):
        found = shutil.which(candidate)
        if found:
            return found
    return None


class WebpageCleanModeTest(unittest.TestCase):
    def test_clean_mode_hides_items_and_ai(self):
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
