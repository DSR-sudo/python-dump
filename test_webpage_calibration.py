"""校准模式前端冒烟测试。

用 node 运行 test_webpage_calibration.js：以最小 DOM 桩执行 web/webpage.html 的内联脚本，
断言点地图取点按 offset/scale 换算成地图像素写进当前地图的 point2D、拖拽不取点、
「用当前位置」填世界坐标、代码块可粘回 mapConverters、关闭后十字标记消失。
node 不可用时跳过（该测试只校验网页交互，不参与设备端运行）。
"""

import os
import shutil
import subprocess
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
HARNESS = os.path.join(HERE, "test_webpage_calibration.js")
PAGE = os.path.join(HERE, "web", "webpage.html")


def _find_node():
    for candidate in ("node", "nodejs", os.path.expanduser("~/.local/bin/node")):
        found = shutil.which(candidate)
        if found:
            return found
    return None


class WebpageCalibrationTest(unittest.TestCase):
    def test_calibrate_mode_picks_map_points(self):
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
