"""Render TALCON pages to PNG so the visual design can be reviewed."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
sys.path.insert(0, os.getcwd())
sys.path.insert(0, os.path.join(os.getcwd(), "tests"))

from PySide6.QtWidgets import QApplication
from talcon.face_widget import FaceWidget
from talcon.ui import theme
from talcon.ui.main_window import MainWindow
from test_ui import StubRuntime

app = QApplication([])
app.setStyleSheet(theme.STYLESHEET)
print("platform:", app.platformName(), "stylesheet:", len(app.styleSheet()))
runtime = StubRuntime()
face = FaceWidget(runtime, lambda: None)
window = MainWindow(runtime, face)
window.resize(1400, 950)
window.show()
app.processEvents()

page = sys.argv[1] if len(sys.argv) > 1 else "cron"
window.show_page(page)
app.processEvents()
button = window.nav_buttons.get("dashboard")
print("app font:", app.font().family(), "| window font:", window.font().family())
if button is not None:
    print("nav font:", button.font().family(), button.font().pointSize())
# A whole-window grab of a large layered window rasterises text as boxes on
# this build; capture the shell frame instead, which renders correctly.
app.processEvents()
from PySide6.QtWidgets import QFrame

shell = window.findChild(QFrame, "Shell")
shot = shell.grab() if shell is not None else window.grab()
shot.save(f"screenshot_{page}.png")
print("saved screenshot_%s.png %s" % (page, (shot.width(), shot.height())))
window.close()
face.close()
