import sys
import os
import nuke
import nukescripts

PROJECT_ROOT = r"C:\Users\vrodionov\Desktop\Footage_Library_Stand_Alone"
VIEWER_PATH = os.path.join(PROJECT_ROOT, "viewer")

if VIEWER_PATH not in sys.path:
    sys.path.append(VIEWER_PATH)

from app import Viewer


def create_panel():
    return Viewer()


PANEL_ID = "uk.co.footageLibrary"

nukescripts.panels.registerWidgetAsPanel(
    "nuke_plugin.menu.create_panel",
    "Footage Library",
    PANEL_ID,
)

# Добавляем в меню Pane
pane_menu = nuke.menu("Pane")
pane_menu.addCommand(
    "Footage Library",
    f"nukescripts.panels.restorePanel('{PANEL_ID}')",
)