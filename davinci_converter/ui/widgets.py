"""Shared GTK helpers: dialogs, combo rows, and thread-to-main marshalling."""

from gi.repository import Adw, GLib, Gtk


def esc(text) -> str:
    """Escape dynamic text for Adw rows (titles/subtitles are Pango markup)."""
    return GLib.markup_escape_text(str(text))


def run_on_main(fn, *args) -> None:
    """Call fn(*args) on the GTK main thread, exactly once. Safe from any thread."""
    def _call():
        fn(*args)
        return False
    GLib.idle_add(_call)


def make_combo_row(title: str, items: list, selected: int = 0,
                   subtitle: str = None) -> Adw.ComboRow:
    row = Adw.ComboRow(title=title)
    if subtitle:
        row.set_subtitle(subtitle)
    row.set_model(Gtk.StringList.new(items))
    row.set_selected(selected)
    return row


def combo_value(row: Adw.ComboRow) -> str:
    idx = row.get_selected()
    model = row.get_model()
    return model.get_string(idx) if idx != Gtk.INVALID_LIST_POSITION else ""


def set_combo_value(row: Adw.ComboRow, value: str) -> None:
    model = row.get_model()
    for i in range(model.get_n_items()):
        if model.get_string(i) == value:
            row.set_selected(i)
            return


def confirm_dialog(parent, heading: str, body: str, ok_label: str,
                   on_ok, destructive: bool = False) -> None:
    d = Adw.MessageDialog(transient_for=parent, heading=heading, body=body)
    d.add_response("cancel", "Cancel")
    d.add_response("ok", ok_label)
    d.set_response_appearance(
        "ok", Adw.ResponseAppearance.DESTRUCTIVE if destructive
        else Adw.ResponseAppearance.SUGGESTED)
    d.set_default_response("cancel")
    d.set_close_response("cancel")
    d.connect("response", lambda _d, r: on_ok() if r == "ok" else None)
    d.present()


def show_text_window(parent, title: str, text: str) -> None:
    win = Adw.Window(title=title, transient_for=parent, modal=True,
                     default_width=700, default_height=440)
    tv = Adw.ToolbarView()
    tv.add_top_bar(Adw.HeaderBar())
    buf = Gtk.TextBuffer()
    buf.set_text(text)
    view = Gtk.TextView(buffer=buf, editable=False, monospace=True,
                        wrap_mode=Gtk.WrapMode.WORD_CHAR)
    for m in ("top", "bottom", "left", "right"):
        getattr(view, f"set_{m}_margin")(10)
    sc = Gtk.ScrolledWindow()
    sc.set_child(view)
    tv.set_content(sc)
    win.set_content(tv)
    win.present()