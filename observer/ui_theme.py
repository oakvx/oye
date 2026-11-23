"""Native Tk, Windows-friendly dark visual system for Oye.

No custom widget packages; all drawing is done in memory by Tk Canvas.
"""
from __future__ import annotations
import tkinter as tk
from tkinter import ttk

BG = '#0B0F16'
SURFACE = '#141A24'
SURFACE_ALT = '#19212D'
BORDER = '#2A3545'
WHITE = '#F8FAFC'
MUTED = '#9BA8BB'
ACCENT = '#6CD6CF'
ACCENT_DARK = '#113C42'
DANGER = '#F7A0A5'


def install_dark_theme(root: tk.Tk):
    root.configure(background=BG)
    style = ttk.Style(root)
    style.theme_use('clam')
    style.configure('.', font=('Segoe UI', 10), background=BG, foreground=WHITE,
                    borderwidth=0)
    style.configure('TFrame', background=BG)
    style.configure('TLabel', background=BG, foreground=WHITE, font=('Segoe UI', 10))
    style.configure('Muted.TLabel', background=BG, foreground=MUTED)
    style.configure('TNotebook', background=BG, borderwidth=0, tabmargins=(10, 6, 10, 4))
    style.configure('TNotebook.Tab', background=BG, foreground=MUTED,
                    font=('Segoe UI Semibold', 10), padding=(20, 13), borderwidth=0)
    style.map('TNotebook.Tab', background=[('selected', SURFACE_ALT), ('active', SURFACE)],
              foreground=[('selected', WHITE), ('active', WHITE)])
    style.configure('TButton', background=SURFACE_ALT, foreground=WHITE,
                    borderwidth=0, relief='flat', padding=(12, 9))
    style.map('TButton', background=[('pressed', ACCENT_DARK), ('active', BORDER), ('disabled', SURFACE)],
              foreground=[('disabled', MUTED)])
    style.configure('Accent.TButton', background=ACCENT, foreground=BG,
                    font=('Segoe UI Semibold', 10), padding=(14, 10))
    style.map('Accent.TButton', background=[('active', '#90E8E1'), ('pressed', '#49B5AE')],
              foreground=[('active', BG)])
    style.configure('TCheckbutton', background=BG, foreground=WHITE, borderwidth=0, padding=5)
    style.map('TCheckbutton', foreground=[('disabled', MUTED)], background=[('active', BG)])
    style.configure('TRadiobutton', background=BG, foreground=WHITE)
    style.configure('TLabelframe', background=BG, foreground=WHITE, relief='solid',
                    borderwidth=1, bordercolor=BORDER, padding=8)
    style.configure('TLabelframe.Label', background=BG, foreground=ACCENT,
                    font=('Segoe UI Semibold', 10), padding=4)
    style.configure('TEntry', fieldbackground=SURFACE_ALT, foreground=WHITE,
                    insertcolor=WHITE, bordercolor=BORDER, lightcolor=BORDER,
                    darkcolor=BORDER, relief='flat', padding=6)
    style.configure('TSpinbox', fieldbackground=SURFACE_ALT, foreground=WHITE,
                    insertcolor=WHITE, arrowcolor=WHITE, bordercolor=BORDER, padding=5)
    style.configure('TCombobox', fieldbackground=SURFACE_ALT, background=SURFACE_ALT,
                    foreground=WHITE, selectbackground=SURFACE_ALT, selectforeground=WHITE,
                    arrowcolor=WHITE, bordercolor=BORDER, padding=5)
    style.map('TCombobox', fieldbackground=[('readonly', SURFACE_ALT)],
              background=[('readonly', SURFACE_ALT)],
              foreground=[('readonly', WHITE)])
    style.configure('Treeview', background=SURFACE, fieldbackground=SURFACE,
                    foreground=WHITE, bordercolor=BORDER, rowheight=28)
    style.map('Treeview', background=[('selected', ACCENT_DARK)],
              foreground=[('selected', WHITE)])
    style.configure('Treeview.Heading', background=SURFACE_ALT, foreground=WHITE,
                    font=('Segoe UI Semibold', 9), relief='flat', padding=7)
    style.map('Treeview.Heading', background=[('active', BORDER)])
    style.configure('TScrollbar', background=SURFACE_ALT, troughcolor=BG,
                    bordercolor=BG, arrowcolor=MUTED, relief='flat')
    style.configure('TScale', background=BG, troughcolor=BORDER)
    root.option_add('*TCombobox*Listbox.background', SURFACE_ALT)
    root.option_add('*TCombobox*Listbox.foreground', WHITE)
    root.option_add('*TCombobox*Listbox.selectBackground', ACCENT_DARK)


def decorate_legacy_widgets(widget):
    """Style native Text widgets used by the legacy calibration / editor tabs."""
    if isinstance(widget, tk.Text):
        widget.configure(background=SURFACE, foreground=WHITE,
                         insertbackground=WHITE, selectbackground=ACCENT_DARK,
                         relief='flat', highlightthickness=1, highlightbackground=BORDER,
                         highlightcolor=ACCENT, padx=10, pady=7)
    if isinstance(widget, tk.Canvas) and str(widget.cget('background')) in ('#d9d9d9', 'SystemButtonFace'):
        widget.configure(background=BG, highlightthickness=0)
    for child in widget.winfo_children():
        decorate_legacy_widgets(child)


def rounded_rect(canvas, x0, y0, x1, y1, radius, fill, outline=None):
    """Draw a genuinely rounded solid card; no antialias dependency."""
    r = max(2, min(radius, (x1 - x0) / 2, (y1 - y0) / 2))
    if outline:
        rounded_rect(canvas, x0, y0, x1, y1, radius, outline)
        return rounded_rect(canvas, x0+1, y0+1, x1-1, y1-1, radius-1, fill)
    objs = [
        canvas.create_rectangle(x0+r, y0, x1-r, y1, fill=fill, outline=fill),
        canvas.create_rectangle(x0, y0+r, x1, y1-r, fill=fill, outline=fill),
    ]
    for x, y in ((x0, y0), (x1-2*r, y0), (x0, y1-2*r), (x1-2*r, y1-2*r)):
        objs.append(canvas.create_oval(x, y, x+2*r, y+2*r, fill=fill, outline=fill))
    return objs


class RoundButton(tk.Canvas):
    def __init__(self, parent, title, subtitle, command, *, width=242, height=72,
                 fill=SURFACE_ALT, fg=WHITE, accent=False):
        super().__init__(parent, width=width, height=height, bg=BG,
                         borderwidth=0, highlightthickness=0, cursor='hand2')
        self.title, self.subtitle, self.command = title, subtitle, command
        self._color = ACCENT if accent else fill
        self._fg = BG if accent else fg
        self._hover = '#97E9E2' if accent else BORDER
        self._active_color = self._color
        self._draw()
        for event in ('<Button-1>', '<Return>', '<space>'):
            self.bind(event, lambda _e: self.command())
        self.bind('<Enter>', self._enter)
        self.bind('<Leave>', self._leave)
        self.bind('<FocusIn>', self._enter)
        self.bind('<FocusOut>', self._leave)
        self.configure(takefocus=True)

    def _draw(self):
        self.delete('all')
        w, h = int(self.cget('width')), int(self.cget('height'))
        rounded_rect(self, 1, 1, w-2, h-2, 18, self._active_color)
        self.create_text(19, 25, text=self.title, anchor='w', fill=self._fg,
                         font=('Segoe UI Semibold', 12))
        self.create_text(19, 52, text=self.subtitle, anchor='w',
                         fill=('#21565D' if self._fg == BG else MUTED),
                         font=('Segoe UI', 9))

    def _enter(self, _event):
        self._active_color = self._hover
        self._draw()

    def _leave(self, _event):
        self._active_color = self._color
        self._draw()


class RoundedCard(tk.Canvas):
    """Canvas card with rounded background and padded content window."""
    def __init__(self, parent, *, width=450, height=355, background=SURFACE,
                 border=BORDER, padding=18):
        super().__init__(parent, width=width, height=height, background=BG,
                         borderwidth=0, highlightthickness=0)
        self._bg = background
        self._border = border
        self._pad = padding
        self.inner = tk.Frame(self, bg=background)
        self._id = self.create_window((padding, padding), window=self.inner, anchor='nw')
        self.bind('<Configure>', self._redraw)

    def _redraw(self, event):
        w, h = max(10, event.width), max(10, event.height)
        self.delete('shell')
        shapes = rounded_rect(self, 1, 1, w-2, h-2, 20, self._bg, self._border)
        for shape in shapes:
            self.addtag_withtag('shell', shape)
            self.tag_lower(shape)
        self.itemconfigure(self._id, width=max(1, w-self._pad*2),
                           height=max(1, h-self._pad*2))
