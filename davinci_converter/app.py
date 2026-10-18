from gi.repository import Adw

from .constants import APP_ID
from .ui.window import ConverterWindow


class ConverterApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID)

    def do_activate(self):
        win = self.props.active_window or ConverterWindow(self)
        win.present()