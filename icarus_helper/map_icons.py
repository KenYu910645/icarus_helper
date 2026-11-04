"""Cached map markers with native mineral thumbnails and semantic UI symbols.

Coordinates inside each icon use a 32 px design grid.  The public pixmap has the
requested pixel size (and devicePixelRatio=1); draw it in a fixed screen-size
target rectangle so zooming the world does not shrink the pictogram.  Pixmaps
must be requested on the GUI thread after creating QApplication.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import re
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor, QFont, QFontDatabase, QGuiApplication, QPainter, QPainterPath, QPen,
    QPixmap, QPolygonF,
)

from .item_icons import has_item_icon, item_pixmap


@dataclass(frozen=True)
class MarkerStyle:
    label: str
    color: str
    code: str
    shape: str = "rock"
    key: str = "unknown"


_MINERALS = {
    "gold": MarkerStyle("Gold", "#ffd34f", "Au", "rock", "gold"),
    "iron": MarkerStyle("Iron", "#c4cfdf", "Fe", "rock", "iron"),
    "copper": MarkerStyle("Copper", "#f3a06a", "Cu", "rock", "copper"),
    "aluminium": MarkerStyle("Aluminium", "#d4f1f4", "Al", "rock", "aluminium"),
    "platinum": MarkerStyle("Platinum", "#ebe3ff", "Pt", "rock", "platinum"),
    "titanium": MarkerStyle("Titanium", "#9cbaff", "Ti", "rock", "titanium"),
    "coal": MarkerStyle("Coal", "#a7b5ca", "C", "coal", "coal"),
    "sulfur": MarkerStyle("Sulfur", "#f5ed55", "S", "crystal", "sulfur"),
    "silica": MarkerStyle("Silica", "#def9ff", "Si", "crystal", "silica"),
    "oxite": MarkerStyle("Oxite", "#53d5ff", "O₂", "crystal", "oxite"),
    "stone": MarkerStyle("Stone", "#bfb7a9", "St", "rock", "stone"),
    "ice": MarkerStyle("Ice", "#9eeaff", "Ice", "cube", "ice"),
    "supercooledice": MarkerStyle("Supercooled ice", "#5ff4f0", "SC", "cube", "supercooledice"),
    "salt": MarkerStyle("Salt", "#ffffff", "NaCl", "cube", "salt"),
    "clay": MarkerStyle("Clay", "#d99776", "Clay", "clay", "clay"),
    "obsidian": MarkerStyle("Obsidian", "#b99aff", "Ob", "gem", "obsidian"),
    "scoria": MarkerStyle("Scoria", "#ff9b78", "Sc", "porous", "scoria"),
    "bauxite": MarkerStyle("Bauxite", "#e3aa82", "Bau", "rock", "bauxite"),
    "limestone": MarkerStyle("Limestone", "#e6e5ca", "Ca", "rock", "limestone"),
    "lithium": MarkerStyle("Lithium", "#e2b9f5", "Li", "crystal", "lithium"),
    "ruby": MarkerStyle("Ruby", "#ff647e", "Rb", "gem", "ruby"),
    "lead": MarkerStyle("Lead", "#a3b2ce", "Pb", "rock", "lead"),
    "uranium": MarkerStyle("Uranium", "#bcf373", "U", "crystal", "uranium"),
    "exotic": MarkerStyle("Exotics", "#f595e4", "Ex", "gem", "exotic"),
    "frozenore": MarkerStyle("Frozen ore", "#a9e6f4", "FRZ", "cube", "frozenore"),
    "frozenwood": MarkerStyle("Frozen wood", "#b5e9f0", "Wd", "wood", "frozenwood"),
    "unknown": MarkerStyle("Unknown", "#c3ccd6", "?", "unknown", "unknown"),
}

_ALIASES = {
    "metal": "iron", "aluminum": "aluminium", "sulphur": "sulfur",
    "quartz": "silica", "silicon": "silica", "supercooledice": "supercooledice",
    "exoticrawuranium": "uranium", "rawuranium": "uranium",
    "redexotic": "exotic", "iceore": "ice",
    "金": "gold", "金礦": "gold", "黃金": "gold", "鐵": "iron", "鐵礦": "iron",
    "銅": "copper", "銅礦": "copper", "鋁": "aluminium", "鋁礦": "aluminium",
    "鉑": "platinum", "白金": "platinum", "鉑礦": "platinum", "鈦": "titanium",
    "煤": "coal", "煤礦": "coal", "硫": "sulfur", "硫磺": "sulfur",
    "矽石": "silica", "矽礦": "silica", "氧礦": "oxite", "石材": "stone",
    "石頭": "stone", "冰": "ice", "超冷冰": "supercooledice", "鹽": "salt",
    "黏土": "clay", "黑曜石": "obsidian", "火山渣": "scoria", "鋁土礦": "bauxite",
    "鋁土": "bauxite", "石灰石": "limestone", "鋰": "lithium", "紅寶石": "ruby",
    "鉛": "lead", "鈾": "uranium", "異質": "exotic", "異質礦": "exotic",
    "凍木": "frozenwood", "冰封木": "frozenwood",
}

# Explicit resource identity -> collectible item in the validated game catalog.
# Aluminium deposits yield Bauxite; the item named Aluminium is the refined ingot.
# Silicon is the deposit table key for the item Silica. Never select refined
# metal or guess a thumbnail based on a similar-looking item/name.
_MINERAL_ITEMS = {
    "gold": "Gold_Ore", "iron": "Metal_Ore", "copper": "Copper_Ore",
    "aluminium": "Bauxite", "bauxite": "Bauxite", "platinum": "Platinum_Ore",
    "titanium": "Titanium_Ore", "coal": "Coal_Ore", "sulfur": "Sulfur",
    "silica": "Silica", "oxite": "Oxite", "stone": "Stone", "ice": "Ice",
    "supercooledice": "Super_Cooled_Ice", "salt": "Salt", "clay": "Clay",
    "obsidian": "Obsidian", "scoria": "Scoria", "limestone": "Limestone",
    "lithium": "Lithium_Ore", "ruby": "Ruby_Ore", "uranium": "Uranium_Raw",
    "exotic": "MetaResource", "frozenore": "Frozen_Ore", "frozenwood": "Frozen_Wood",
}


@lru_cache(maxsize=256)
def mineral_style(resource_type: str | None) -> MarkerStyle:
    """Map explicit resource keys to style; never guess a mineral for unknowns."""
    text = str(resource_type or "").strip()
    # Density/ore variants are table suffixes, not a different mineral.
    text = re.sub(r"_(?:Normal|Dense)$", "", text, flags=re.IGNORECASE)
    key = re.sub(r"[^a-z0-9\u3400-\u9fff]", "", text.lower())
    key = _ALIASES.get(key, key)
    if key not in _MINERALS and key.endswith("ore"):
        stem = key[:-3]
        key = _ALIASES.get(stem, stem)
    return _MINERALS.get(key, _MINERALS["unknown"])


def mineral_item_id(resource_type: str | None) -> str | None:
    """Return an explicit raw-material item ID, independent of asset availability."""
    key = re.sub(r"[^a-z0-9]", "", str(resource_type or "").lower())
    # Red raw exotics and stabilized exotics are different items and pictures.
    if key == "exoticredraw":
        return "Exotic_Red_Raw"
    if key == "exoticred":
        return "Exotic_Red"
    return _MINERAL_ITEMS.get(mineral_style(resource_type).key)


def _uses_mineral_thumbnail(kind: str) -> bool:
    kind = str(kind).lower()
    return "deep" in kind or "deposit" in kind or kind in ("ore", "mineral", "voxel", "exotic", "ice")


def marker_item_id(kind: str, resource_type: str = "", candidate: bool = False) -> str | None:
    """Choose artwork without changing the resource's identity or confidence.

    An empty exotic type is a layer/category icon. Unidentified exotic candidates
    can use that same category artwork with their question badge and dashed frame;
    a biome name is never promoted to a confirmed purple/red exotic subtype.
    Unknown confirmed resources and unknown ordinary minerals keep a question.
    """
    if not _uses_mineral_thumbnail(kind):
        return None
    exact = mineral_item_id(resource_type)
    if exact is not None:
        return exact
    if str(kind).lower() == "exotic" and (not str(resource_type or "").strip() or candidate):
        return "MetaResource"
    return None


_KIND_STYLES = {
    "cave": MarkerStyle("Cave", "#d7b7ff", "C", "cave", "cave"),
    "drill": MarkerStyle("Drill", "#7ef2bd", "D", "drill", "drill"),
    "geyser": MarkerStyle("Geyser", "#ffab86", "G", "geyser", "geyser"),
    "oil": MarkerStyle("Oil", "#e7cb7d", "Oil", "oil", "oil"),
    "exotic": MarkerStyle("Exotics", "#f595e4", "Ex", "gem", "exotic"),
    "player": MarkerStyle("Player", "#ffffff", "P", "player", "player"),
    "base": MarkerStyle("Base", "#83f5b8", "H", "base", "base"),
    "water": MarkerStyle("Water", "#81caff", "W", "water", "water"),
    "plant": MarkerStyle("Plants", "#a4e895", "L", "plant", "plant"),
    "tree": MarkerStyle("Trees", "#9be0aa", "T", "tree", "tree"),
    "deployable": MarkerStyle("Deployable", "#c8d5e5", "Box", "deployable", "deployable"),
    "resource": MarkerStyle("Resource", "#abddeb", "?", "unknown", "resource"),
}


def marker_style(kind: str, resource_type: str = "") -> MarkerStyle:
    """Return the visible name/color without creating a pixmap."""
    kind = str(kind).lower()
    if "deep" in kind or "deposit" in kind or kind in ("ore", "mineral", "voxel"):
        return mineral_style(resource_type)
    if "cave" in kind or "entrance" in kind:
        return _KIND_STYLES["cave"]
    return _KIND_STYLES.get(kind, _KIND_STYLES["resource"])


def _poly(painter: QPainter, points, color, outline="#0b1621", width=1.0):
    painter.setPen(QPen(QColor(outline), width, Qt.PenStyle.SolidLine,
                        Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    painter.setBrush(QColor(color))
    painter.drawPolygon(QPolygonF([QPointF(x, y) for x, y in points]))


def _line(painter: QPainter, points, color, width=1.5):
    painter.setPen(QPen(QColor(color), width, Qt.PenStyle.SolidLine,
                        Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    path = QPainterPath(QPointF(*points[0]))
    for point in points[1:]:
        path.lineTo(*point)
    painter.drawPath(path)


def _sparkle(painter: QPainter, x, y, radius=2.2):
    _line(painter, [(x-radius, y), (x+radius, y)], "#ffffff", .9)
    _line(painter, [(x, y-radius), (x, y+radius)], "#ffffff", .9)


def _caption(painter: QPainter, label: str):
    # Short Chinese labels are more readable than colour alone for silver ores.
    painter.fillRect(QRectF(4, 22, 24, 7.5), QColor("#07121e"))
    font = QFont("Microsoft JhengHei")
    font.setBold(True)
    font.setPixelSize(10 if len(label) <= 2 else 8)
    painter.setFont(font)
    painter.setPen(QColor("#ffffff"))
    painter.drawText(QRectF(3, 21.3, 26, 9), Qt.AlignmentFlag.AlignCenter, label)


def _rock(painter: QPainter, style: MarkerStyle):
    color = QColor(style.color)
    _poly(painter, [(5,17),(9,7),(20,5),(27,13),(23,22),(11,23)], color.darker(135))
    _poly(painter, [(9,7),(17,10),(20,5)], color.lighter(130), style.color, .6)
    _poly(painter, [(17,10),(27,13),(20,5)], color, style.color, .6)
    _poly(painter, [(17,10),(13,18),(5,17),(9,7)], color.lighter(108), style.color, .6)
    _poly(painter, [(13,18),(23,22),(27,13),(17,10)], color.darker(125), style.color, .6)
    if style.key == "iron":
        _poly(painter, [(7,15),(11,11),(13,15),(10,18)], "#b46d54", "#d4b09b", .5)
    elif style.key == "copper":
        _poly(painter, [(18,15),(23,13),(24,17),(20,19)], "#79b9a6", "#b6e2c8", .5)
    elif style.key in ("gold", "aluminium", "platinum", "titanium"):
        _sparkle(painter, 20, 11, 2)
    if style.shape == "porous":
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color.darker(230))
        for x,y,r in ((10,12,1.4),(19,9,1),(20,17,1.8),(12,19,1)):
            painter.drawEllipse(QPointF(x,y),r,r)


def _crystal(painter: QPainter, style: MarkerStyle):
    color = QColor(style.color)
    _poly(painter, [(5,15),(7,8),(12,10),(14,20),(8,22)], color.darker(120))
    _poly(painter, [(20,11),(24,7),(28,13),(25,21),(19,21)], color.darker(125))
    _poly(painter, [(10,8),(16,3),(22,9),(20,20),(14,23),(10,17)], color)
    _poly(painter, [(16,3),(16,16),(14,23),(10,17),(10,8)], color.lighter(145), style.color, .6)
    _line(painter, [(16,3),(22,9),(16,16),(20,20)], color.darker(160), .8)
    if style.key in ("silica", "oxite", "lithium"):
        _sparkle(painter, 24, 6, 2)
    if style.key == "uranium":
        painter.setPen(QPen(QColor("#101b14"),1.8))
        painter.drawEllipse(QPointF(17,13),3.2,3.2)
        painter.drawLine(QPointF(17,9),QPointF(17,17))
        painter.drawLine(QPointF(13,13),QPointF(21,13))


def _cube(painter: QPainter, style: MarkerStyle):
    color = QColor(style.color)
    _poly(painter, [(6,9),(16,4),(26,9),(16,14)], color.lighter(145))
    _poly(painter, [(6,9),(16,14),(16,23),(6,18)], color.darker(120))
    _poly(painter, [(16,14),(26,9),(26,18),(16,23)], color)
    _line(painter, [(10,9),(14,7)], "#ffffff",1)
    _sparkle(painter, 25, 5, 2)


def _coal(painter: QPainter, _style):
    for points, face in (
        ([(4,16),(7,8),(14,6),(18,13),(14,21),(7,22)], "#52627c"),
        ([(16,10),(23,5),(29,12),(25,20),(18,20)], "#3b465b"),
        ([(10,19),(17,13),(24,18),(22,24),(12,24)], "#67738b"),
    ):
        _poly(painter, points, face, "#c2cede", 1)
    _line(painter, [(7,8),(11,13),(14,6)], "#8493ac", .8)


def _clay(painter: QPainter, style: MarkerStyle):
    _poly(painter,[(5,18),(7,10),(15,7),(24,10),(27,17),(22,23),(10,22)],style.color,"#ffe1c8",1.1)
    painter.setPen(QPen(QColor("#f2ba98"),1.2))
    painter.setBrush(QColor("#b57456"))
    painter.drawEllipse(QRectF(8,9,10,7))
    painter.drawEllipse(QRectF(17,13,8,6))
    _line(painter,[(10,19),(14,16),(17,20)],"#87523e",1.1)


def _wood(painter: QPainter, _style):
    _line(painter,[(9,17),(23,10)],"#d2bca5",11)
    _line(painter,[(12,20),(25,13)],"#82634d",1.2)
    painter.setPen(QPen(QColor("#d9f9ff"),1.2))
    painter.setBrush(QColor("#c6ab8b"))
    painter.drawEllipse(QRectF(3,11,12,12))
    painter.setPen(QPen(QColor("#775e4a"),1))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(QRectF(6,14,6,6))
    _line(painter,[(7,11),(13,9),(20,5),(25,8)],"#dcfbff",2.8)
    _sparkle(painter,25,5,1.6)


def _gem(painter: QPainter, style: MarkerStyle):
    color = QColor(style.color)
    _poly(painter, [(5,10),(10,5),(22,5),(27,10),(16,23)], color.darker(130))
    _poly(painter, [(5,10),(13,10),(10,5)], color.lighter(135), style.color,.6)
    _poly(painter, [(10,5),(13,10),(19,10),(22,5)], color.lighter(110), style.color,.6)
    _poly(painter, [(13,10),(19,10),(16,23)], color.lighter(145), style.color,.6)
    _poly(painter, [(19,10),(27,10),(22,5)], color, style.color,.6)
    _sparkle(painter, 25, 5, 2)


def _cave(painter: QPainter, _style):
    _poly(painter, [(3,25),(5,14),(10,7),(17,4),(24,9),(28,18),(29,26)], "#af91d3", "#f0dcff", 1.4)
    _poly(painter, [(5,14),(10,7),(13,13),(8,19),(4,22)], "#d7b8f4", "#d7b8f4",.5)
    _poly(painter, [(17,4),(24,9),(26,15),(20,11),(15,10)], "#86709e", "#86709e",.5)
    opening=QPainterPath(QPointF(10,26))
    opening.lineTo(10,18)
    opening.cubicTo(10,9,22,9,22,18)
    opening.lineTo(22,26)
    opening.closeSubpath()
    painter.setPen(QPen(QColor("#f4e6ff"),1.3))
    painter.setBrush(QColor("#050a12"))
    painter.drawPath(opening)
    _line(painter, [(7,27),(25,27)], "#f4e6ff",1.8)


def _drill(painter: QPainter, _style):
    _poly(painter, [(6,24),(13,6),(18,6),(25,24)], "#235747", "#caffdf", 1.6)
    _line(painter, [(12,8),(21,20),(9,20),(19,10)], "#7befb3", 1.3)
    _line(painter, [(16,4),(16,25)], "#ffffff", 2)
    for y in (11,15,19,23):
        _line(painter, [(13,y),(19,y+2)], "#baffde",1.5)
    _line(painter, [(4,26),(27,26)], "#baffde",2)


def _droplet_path(x=16, top=5, width=14, bottom=26):
    path=QPainterPath(QPointF(x,top))
    path.cubicTo(x-2,top+6,x-width/2,bottom-8,x-width/2,bottom-5)
    path.cubicTo(x-width/2,bottom+2,x+width/2,bottom+2,x+width/2,bottom-5)
    path.cubicTo(x+width/2,bottom-8,x+2,top+6,x,top)
    return path


def _oil(painter: QPainter, _style):
    painter.setPen(QPen(QColor("#ffe6a6"),1.7))
    painter.setBrush(QColor("#6c5835"))
    painter.drawPath(_droplet_path())
    _line(painter, [(12,19),(11,22),(13,24)], "#fff0bf",1.5)
    _line(painter, [(6,28),(26,28)], "#e7cb7d",1.6)


def _geyser(painter: QPainter, _style):
    _line(painter, [(5,25),(11,21),(21,21),(27,25)], "#ffd4b8",2)
    for points in ([(12,21),(10,16),(7,13)],[(16,21),(16,6)],[(20,21),(22,15),(25,11)]):
        _line(painter, points, "#ffb28c", 2.3)
    for x,y in ((7,9),(16,3),(26,7)):
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#ffe2ce"))
        painter.drawEllipse(QPointF(x,y),1.7,1.7)
    _line(painter, [(5,28),(27,28)], "#ffb28c",1.5)


def _player(painter: QPainter, _style):
    painter.setPen(QPen(QColor("#07121e"),1))
    painter.setBrush(QColor("#ffffff"))
    painter.drawEllipse(QPointF(16,9),4.5,4.5)
    path=QPainterPath(QPointF(8,26))
    path.lineTo(9,19)
    path.cubicTo(10,13,22,13,23,19)
    path.lineTo(24,26)
    path.closeSubpath()
    painter.drawPath(path)


def _base(painter: QPainter, _style):
    _poly(painter, [(5,15),(16,5),(27,15),(24,15),(24,27),(8,27),(8,15)], "#73dba5", "#eaffee",1.5)
    painter.fillRect(QRectF(13,18,6,9),QColor("#12332a"))
    painter.fillRect(QRectF(20,18,2.5,3),QColor("#edfff3"))
    _line(painter, [(4,15),(16,4),(28,15)], "#eaffee",2)


def _water(painter: QPainter, _style):
    painter.setPen(QPen(QColor("#def5ff"),1.5))
    painter.setBrush(QColor("#4aa8e7"))
    painter.drawPath(_droplet_path(top=4,bottom=22,width=13))
    _line(painter, [(5,27),(10,25),(16,27),(22,25),(27,27)], "#bfeaff",1.8)


def _plant(painter: QPainter, _style):
    _line(painter, [(16,27),(16,10)], "#d4ffd0",2)
    _poly(painter, [(15,19),(5,15),(5,8),(12,10),(16,15)], "#71bd6c", "#d5ffbe",1.2)
    _poly(painter, [(17,17),(18,8),(27,5),(27,12),(20,18)], "#9be788", "#d5ffbe",1.2)


def _tree(painter: QPainter, _style):
    painter.fillRect(QRectF(14,20,4,9),QColor("#e5c7a0"))
    _poly(painter, [(4,23),(10,15),(7,15),(12,9),(11,9),(16,3),(21,9),(20,9),(25,15),(22,15),(28,23)], "#5fad83", "#ceffe2",1.3)


def _deployable(painter: QPainter, _style):
    _poly(painter, [(6,10),(16,5),(26,10),(26,25),(16,29),(6,24)], "#8fa3bc", "#edf4ff",1.4)
    _line(painter, [(6,10),(16,15),(26,10)], "#edf4ff",1.2)
    _line(painter, [(16,15),(16,29)], "#edf4ff",1.2)
    _line(painter, [(11,8),(21,13),(21,19)], "#52687f",2.2)


def _unknown(painter: QPainter, _style):
    font=QFont("Segoe UI")
    font.setPixelSize(21)
    font.setBold(True)
    painter.setFont(font)
    painter.setPen(QColor("#ffffff"))
    painter.drawText(QRectF(4,1,24,24),Qt.AlignmentFlag.AlignCenter,"?")


_PAINTERS = {
    "rock":_rock, "porous":_rock, "crystal":_crystal, "coal":_coal,
    "cube":_cube, "clay":_clay, "wood":_wood, "gem":_gem, "cave":_cave, "drill":_drill, "oil":_oil,
    "geyser":_geyser, "player":_player, "base":_base, "water":_water,
    "plant":_plant, "tree":_tree, "deployable":_deployable,"unknown":_unknown,
}


def _candidate_badge(painter: QPainter):
    painter.setPen(QPen(QColor("#ffedb0"),.8))
    painter.setBrush(QColor("#8e6314"))
    painter.drawEllipse(QRectF(.3,.3,10.5,10.5))
    font=QFont("Segoe UI")
    font.setBold(True)
    font.setPixelSize(9)
    painter.setFont(font)
    painter.setPen(QColor("#ffffff"))
    painter.drawText(QRectF(.1,-.1,11,11),Qt.AlignmentFlag.AlignCenter,"?")


def _drilled_badge(painter: QPainter):
    painter.setPen(QPen(QColor("#dbffec"),.8))
    painter.setBrush(QColor("#14754e"))
    painter.drawRoundedRect(QRectF(20.6,20.6,11,11),2.5,2.5)
    _line(painter,[(22.5,29),(26,23),(29,29)],"#c9ffe6",1)
    _line(painter,[(26,22),(26,30)],"#ffffff",1)
    for y in (25,27,29):
        _line(painter,[(24.5,y),(27.5,y+.8)],"#ffffff",.8)


@lru_cache(maxsize=512)
def marker_pixmap(kind: str, resource_type: str = "", size: int = 28,
                  candidate: bool = False, drilled: bool = False) -> QPixmap:
    """Create a cached fixed-size semantic marker; requires QApplication.

    `candidate` deliberately uses both a dashed outline and a question badge.
    `drilled` means a drill is placed; it does not imply that it is operating.
    """
    if QGuiApplication.instance() is None:
        raise RuntimeError("marker_pixmap requires a QApplication on the GUI thread")
    size=max(16,min(128,int(size)))
    style=marker_style(kind,resource_type)
    pixmap=QPixmap(size,size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter=QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    painter.scale(size/32,size/32)
    frame=QRectF(2,2,28,28)
    painter.setPen(QPen(QColor("#03080f"),3.5))
    painter.setBrush(QColor(5,15,26,244))
    painter.drawRoundedRect(frame,6,6)
    border=QPen(QColor(style.color),1.6)
    if candidate:
        border.setStyle(Qt.PenStyle.DashLine)
    painter.setPen(border)
    painter.drawRoundedRect(frame,6,6)
    if _uses_mineral_thumbnail(kind):
        item_id = marker_item_id(kind, resource_type, candidate=candidate)
        if item_id and has_item_icon(item_id):
            picture = item_pixmap(item_id, size=max(16, size - 6))
            # The thumbnail keeps its source alpha and aspect ratio. No custom
            # mineral drawing or embedded caption covers the game's artwork.
            ratio = min(26 / picture.width(), 26 / picture.height())
            width, height = picture.width() * ratio, picture.height() * ratio
            target = QRectF(16 - width / 2, 16 - height / 2, width, height)
            painter.drawPixmap(target, picture, QRectF(picture.rect()))
        else:
            _unknown(painter, style)
    else:
        _PAINTERS.get(style.shape,_unknown)(painter,style)
    if candidate:
        _candidate_badge(painter)
    if drilled:
        _drilled_badge(painter)
    painter.end()
    return pixmap


def render_icon_sheet(output_path: str | Path):
    """Render semantic symbols and bundled original-game mineral thumbnails."""
    if QGuiApplication.instance() is None:
        raise RuntimeError("Create QApplication before rendering the icon sheet")
    items=[("cave","",False,False),("deep","Gold",False,False),
           ("deep","Iron",False,False),("deep","Copper",False,False),
           ("deep","Aluminium",False,False),("deep","Platinum",False,False),
           ("deep","Titanium",False,False),("deep","Coal",False,False),
           ("deep","Sulfur",False,False),("deep","Silica",False,False),
           ("deep","Oxite",False,False),("deep","Ice",False,False),
           ("deep","Salt",False,False),("deep","Clay",False,False),
           ("deep","Obsidian",False,False),("deep","Scoria",False,False),
           ("deep","Super_Cooled_Ice",False,False),("deep","Frozen_Wood",False,False),
           ("deep","Limestone",False,False),
           ("deep","Gold",True,False),("deep","Gold",False,True),
           ("deep","Unknown",True,True),("cave","",True,False),
           ("drill","",False,False),("geyser","",False,False),
           ("oil","",False,False),("exotic","",False,False),
           ("player","",False,False),("base","",False,False),
           ("water","",False,False),("plant","",False,False),
           ("tree","",False,False),("deployable","",False,False)]
    columns,cell_w,cell_h=6,180,138
    rows=(len(items)+columns-1)//columns
    canvas=QPixmap(columns*cell_w,rows*cell_h+70)
    canvas.fill(QColor("#101e2a"))
    painter=QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setFont(QFont("Microsoft JhengHei",16,QFont.Weight.Bold))
    painter.setPen(QColor("#e8f4ff"))
    painter.drawText(QRectF(18,12,1000,30),"Map icons: caves, minerals, candidates and drills")
    painter.setFont(QFont("Microsoft JhengHei",10))
    painter.setPen(QColor("#a9c1d4"))
    painter.drawText(QRectF(18,42,1000,24),"Left: 28 px actual size. Right: 56 px detail. ? = candidate; green drill badge = installed")
    for index,(kind,mineral,candidate,drilled) in enumerate(items):
        x,y=(index%columns)*cell_w,(index//columns)*cell_h+70
        painter.fillRect(QRectF(x+5,y+5,cell_w-10,cell_h-10),QColor("#1d3542"))
        painter.drawPixmap(x+30,y+28,marker_pixmap(kind,mineral,28,candidate,drilled))
        painter.drawPixmap(x+80,y+13,marker_pixmap(kind,mineral,56,candidate,drilled))
        style=marker_style(kind,mineral)
        label=style.label+("Candidate" if candidate else "")+(" + Drill" if drilled else "")
        painter.setPen(QColor("#f4f8ff"))
        painter.setFont(QFont("Microsoft JhengHei",11,QFont.Weight.Bold))
        painter.drawText(QRectF(x+5,y+76,cell_w-10,25),Qt.AlignmentFlag.AlignCenter,label)
        painter.setPen(QColor("#a9c1d4"))
        painter.setFont(QFont("Segoe UI",9))
        painter.drawText(QRectF(x+5,y+102,cell_w-10,20),Qt.AlignmentFlag.AlignCenter,style.code)
    painter.end()
    output=Path(output_path)
    output.parent.mkdir(parents=True,exist_ok=True)
    if not canvas.save(str(output)):
        raise OSError(f"Could not save icon sheet: {output}")
    return output


if __name__ == "__main__":
    import sys
    from PySide6.QtWidgets import QApplication
    app=QApplication(sys.argv[:1])
    for font_path in (Path("C:/Windows/Fonts/msjh.ttc"),Path("C:/Windows/Fonts/segoeui.ttf")):
        if font_path.exists():
            QFontDatabase.addApplicationFont(str(font_path))
    render_icon_sheet(Path(__file__).resolve().parents[1]/"artifacts/ui/icon-sheet.png")
