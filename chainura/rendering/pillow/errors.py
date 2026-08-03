class RendererLabError(Exception):
    """Expected, user-correctable renderer lab failure."""


class RendererAssetError(RendererLabError):
    pass


class RendererLayoutError(RendererLabError):
    pass
