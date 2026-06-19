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
