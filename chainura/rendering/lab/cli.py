from __future__ import annotations
import argparse, sys
from pathlib import Path
from chainura.domain.destination import Exchange
from chainura.rendering.lab.fixtures import FIXTURES, fixture
from chainura.rendering.lab.preview import render_preview
from chainura.rendering.lab.watch import watch
from chainura.rendering.pillow.errors import RendererLabError
from chainura.rendering.pillow.text import PathFontProvider

ROOT=Path(".renderer_lab"); EXCHANGES=tuple(x.value for x in Exchange)
def _common(parser, *, paths=True):
    if paths:
        parser.add_argument("--exchange",required=True,choices=EXCHANGES); parser.add_argument("--template",required=True,type=Path); parser.add_argument("--output",required=True,type=Path)
    parser.add_argument("--fixture",required=True,choices=tuple(FIXTURES)); parser.add_argument("--font",required=True,type=Path); parser.add_argument("--debug",action="store_true")
def parser():
    result=argparse.ArgumentParser(prog="python -m chainura.rendering.lab",description="Developer-only local signal Renderer Lab")
    commands=result.add_subparsers(dest="command",required=True)
    _common(commands.add_parser("preview",help="render one exchange preview"))
    all_parser=commands.add_parser("preview-all",help="render all exchanges using canonical local paths"); _common(all_parser,paths=False); all_parser.add_argument("--root",type=Path,default=ROOT)
    watch_parser=commands.add_parser("watch",help="render and poll template/layout changes"); _common(watch_parser); watch_parser.add_argument("--interval",type=float,default=.5)
    return result
def _render(exchange,template,output,args):
    payload=fixture(args.fixture,exchange); return render_preview(payload,template,output,PathFontProvider(args.font),args.debug)
def main(argv=None):
    args=parser().parse_args(argv)
    try:
        if args.command=="preview": result=_render(Exchange(args.exchange),args.template,args.output,args); print(f"rendered {result.file_path} {result.width}x{result.height} PNG {result.sha256}")
        elif args.command=="preview-all":
            for exchange in Exchange: _render(exchange,args.root/"templates"/f"{exchange.value}.png",args.root/"output"/f"{exchange.value}-{args.fixture}.png",args)
        else:
            exchange=Exchange(args.exchange); layout=Path(__file__).parents[1]/"layouts"/f"{exchange.value}.py"
            watch(lambda:_render(exchange,args.template,args.output,args),args.template,layout,args.output,args.interval)
        return 0
    except (RendererLabError,KeyError,OSError) as exc: print(f"error: {exc}",file=sys.stderr); return 2
