"""Constantes visuales de la UI: colores, tamaños, layout."""

WINDOW_SIZE = (1920, 1080)
SIDEBAR_WIDTH = 480
FPS = 60

PLAYER_COLORS = {
    0: (210, 70, 60),    # jugador humano: rojo
    1: (70, 110, 210),   # azul
    2: (80, 175, 80),    # verde
    3: (225, 195, 70),   # amarillo
}
NEUTRAL_COLOR = (140, 140, 140)

BACKGROUND = (18, 22, 18)
SIDEBAR_BG = (32, 36, 40)
TEXT = (225, 225, 225)

# --- estilo "pergamino vintage" del mapa ----------------------------------
# El grade de cada tile de terreno/agua: primero se DESATURA (acerca a gris)
# para apagar los verdes/azules saturados, y luego se multiplica por un tinte
# sepia. Juntos unifican el tileset en un look de mapa antiguo.
SEPIA_TINT = (212, 175, 120)
SEPIA_STRENGTH = 0.5   # intensidad del multiply sepia (0 = sin tinte)
SEPIA_DESAT = 0.55     # fracción de desaturación hacia gris (0 = color pleno)
# Contornos de tinta sobre los límites de terreno (costas, bosques, montañas).
# INK_STRENGTH es la opacidad del trazo: 0 = sin contornos (apagado), 1 = tinta
# plena. Valores intermedios suavizan la línea.
INK = (0, 0, 0)
INK_STRENGTH = 0.1
# Overlay de papel: cuánto se nota la textura de pergamino (0..1).
PAPER_STRENGTH = 0.65
# Viñeta: oscurecimiento de los bordes del mapa (efecto scroll quemado).
VIGNETTE_STRENGTH = 0.55
# Variación por tile para romper la repetición. Solo espejo horizontal (2
# variantes): rompe el patrón sin revelar la grilla. El jitter de brillo está
# en 0 a propósito — un brillo distinto por tile dibuja la grilla en las zonas
# uniformes; subilo solo si sumás variantes de arte que disimulen los bordes.
TILE_STYLE_VARIANTS = 2
TILE_BRIGHTNESS_JITTER = 0

# Chrome cartográfico: rosa de los vientos (fracción del lado menor del mapa) y
# marco decorativo de tinta alrededor del mapa. Reemplazables por arte propio
# (compass.png); poné MAP_FRAME en False para sacar el marco.
COMPASS_SIZE_FRAC = 0.13
MAP_FRAME = True
TEXT_DIM = (150, 150, 150)
SELECTION = (250, 220, 60)
PATH_PENDING = (250, 220, 60)    # path nuevo del ejército seleccionado
PATH_OTHERS = (230, 230, 230)    # paths nuevos de otros ejércitos propios
PATH_ONGOING = (120, 120, 120)   # órdenes de turnos anteriores en curso
BUTTON_BG = (60, 110, 60)
BUTTON_BG_OVER = (80, 140, 80)
GAMEOVER_BG = (0, 0, 0, 180)
SPAWN_HIGHLIGHT = (250, 220, 60)  # anillos que señalan el ejército inicial
CLASH_FLASH = (255, 200, 90)      # destello exterior del choque de batalla
CLASH_CORE = (255, 245, 210)      # núcleo del destello


FLAG_ICONS = {  # asset de bandera por dueño (gris si neutral / desconocido)
    0: "flag_red",
    1: "flag_blue",
    2: "flag_green",
    3: "flag_yellow",
}


def player_color(owner: int) -> tuple[int, int, int]:
    return PLAYER_COLORS.get(owner, NEUTRAL_COLOR)


def flag_icon(owner: int) -> str:
    """Nombre del asset de bandera según el dueño del sitio (gris si neutral)."""
    return FLAG_ICONS.get(owner, "flag")
