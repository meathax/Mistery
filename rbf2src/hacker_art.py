"""Top-right artwork for the Mistery GUI: a pixel-art hacker in shades and a long coat, drawn with
half-block characters (two pixels per text cell), standing in animated falling-code rain."""
import random
import tkinter as tk

# Pixel map, 28 wide x 36 high ('.' = transparent: code rain shows through).
#   k hair  K hair highlight  s skin  d skin shadow  f frame  g lens  G lens reflection  W glint
#   m mouth  c coat  C coat fold/edge  n neck shadow
PIXELS = [
    "..........kkkkkkkk..........",
    "........kkKKKkkkkkkk........",
    ".......kKKKkkkkkkkkkkk......",
    "......kKKkkkkkkkkkkkkkk.....",
    ".....kkKkkkkkkkkkkkkkkkk....",
    ".....kkkkkkkkkkkkkkkkkkk....",
    ".....kkkkkkkkkkkkkkkkkkk....",
    ".....kkksssssssssssssskk....",
    ".....kkdssssssssssssssdk....",
    ".....kdsssssssssssssssssd...",
    "....ffffffffffssffffffffff..",
    "....fgGGWggggfssfgGGWggggf..",
    "....fggGGggggfddfggGGggggf..",
    ".....fgggggggf..fgggggggf...",
    "......ffffffff..ffffffff....",
    ".....dssssssssddssssssssd...",
    "......dsssssssssssssssd.....",
    "......dssssssddssssssd......",
    ".......dssssssssssssd.......",
    ".......dsssmmmmmmsssd.......",
    "........dssssssssssd........",
    ".........ddssssssdd.........",
    "..........ddssssdd..........",
    "...........nddddn...........",
    "........ccCnsssnCcc.........",
    "......cccccCnddnCccccc......",
    ".....ccccccCCnnCCcccccc.....",
    "....ccccccccCccCcccccccc....",
    "...cccccccccCccCccccccccc...",
    "..ccccCccccccCCccccccCcccc..",
    "..cccccCcccccccccccccCcccc..",
    ".ccccccCcccccccccccccCccccc.",
    ".ccccccCccccccccccccccCcccc.",
    "cccccccCccccccccccccccCccccc",
    "cccccccCccccccccccccccCccccc",
    "ccccccCcccccccccccccccCccccc",
]
COLOURS = {"k": "#06110a", "K": "#2e6b34", "s": "#3fd84a", "d": "#1d8a2a", "f": "#000000", "g": "#020804",
           "G": "#00dcff", "W": "#ffffff", "m": "#0d4d17", "c": "#030a05", "C": "#1e4d26", "n": "#14612a"}
GLYPHS = "01ｱｲｳｴｵｶｷｸｹｺｻｼｽｾｿﾀﾁﾂﾃﾄﾅﾆﾇﾈﾉﾊﾋﾌﾍﾎﾏﾐﾑﾒﾓﾔﾕﾖﾗﾘﾙﾚﾛﾜﾝ$#*+<>=:"
RAIN = ["#e8ffe8", "#7dff7d", "#39ff14", "#24c41a", "#178c12", "#0f5c0c", "#0a3d08"]   # head -> tail


class HackerArt(tk.Canvas):
    """Canvas: code rain (text glyphs on a 2x2-pixel grid) behind the pixel figure (squares)."""
    W, H = len(PIXELS[0]), len(PIXELS)

    def __init__(self, master, bg, **kw):
        scale = float(master.tk.call("tk", "scaling"))          # pixels per point (DPI aware)
        self.px = max(5, round(5.2 * scale))
        super().__init__(master, width=self.W * self.px, height=self.H * self.px, bg=bg, bd=0,
                         highlightthickness=0, **kw)
        self.bgc = bg
        cell = 2 * self.px
        self.ncols, self.nrows = self.W // 2, self.H // 2
        rnd = self.rnd = random.Random(7)
        self.cols = [{"y": rnd.uniform(-self.nrows, self.nrows), "v": rnd.uniform(0.2, 0.6),
                      "g": [rnd.choice(GLYPHS) for _ in range(self.nrows)]} for _ in range(self.ncols)]
        font = ("Consolas", -int(cell * 0.9), "bold")
        self.items = [[self.create_text(x * cell + cell / 2, y * cell + cell / 2, text="", fill=bg, font=font)
                       for y in range(self.nrows)] for x in range(self.ncols)]
        for y, row in enumerate(PIXELS):                          # figure on top of the rain
            for x, ch in enumerate(row):
                if ch != ".":
                    self.create_rectangle(x * self.px, y * self.px, (x + 1) * self.px, (y + 1) * self.px,
                                          fill=COLOURS[ch], outline=COLOURS[ch], tags="fig")
        self._tick()

    def _tick(self):
        for x, c in enumerate(self.cols):
            c["y"] += c["v"]
            if c["y"] - len(RAIN) > self.nrows:
                c["y"] = self.rnd.uniform(-10, 0)
                c["v"] = self.rnd.uniform(0.2, 0.6)
            if self.rnd.random() < 0.35:
                c["g"][self.rnd.randrange(self.nrows)] = self.rnd.choice(GLYPHS)
            for y in range(self.nrows):
                d = int(c["y"]) - y
                if 0 <= d < len(RAIN):
                    self.itemconfigure(self.items[x][y], text=c["g"][y], fill=RAIN[d])
                else:
                    self.itemconfigure(self.items[x][y], text="")
        self.tag_raise("fig")
        self.after(80, self._tick)
