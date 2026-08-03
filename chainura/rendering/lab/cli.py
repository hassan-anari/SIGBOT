from __future__ import annotations
import argparse, sys
from pathlib import Path
from chainura.domain.destination import Exchange
from chainura.rendering.lab.fixtures import FIXTURES, fixture
from chainura.rendering.lab.preview import load_exchange_layout, render_preview
from chainura.rendering.lab.watch import validate_watch_interval, watch
from chainura.rendering.pillow.errors import RendererLabError
from chainura.rendering.pillow.text import RoleFontProvider

ROOT=Path(".renderer_lab"); EXCHANGES=tuple(x.value for x in Exchange)
def _common(parser, *, paths=True):
    if paths:
        parser.add_argument("--exchange",required=True,choices=EXCHANGES); parser.add_argument("--template",required=True,type=Path); parser.add_argument("--output",required=True,type=Path)
    parser.add_argument("--fixture",required=True,choices=tuple(FIXTURES)); parser.add_argument("--font",type=Path); parser.add_argument("--font-role",action="append",default=[],metavar="ROLE=PATH"); parser.add_argument("--debug",action="store_true")
    parser.add_argument("--logo",type=Path,help="optional local PNG coin logo",)
def parser():
    result=argparse.ArgumentParser(prog="python -m chainura.rendering.lab",description="Developer-only local signal Renderer Lab")
    commands=result.add_subparsers(dest="command",required=True)
    _common(commands.add_parser("preview",help="render one exchange preview"))
    all_parser=commands.add_parser("preview-all",help="render all exchanges using canonical local paths"); _common(all_parser,paths=False); all_parser.add_argument("--root",type=Path,default=ROOT)
    watch_parser=commands.add_parser("watch",help="render and poll template/layout changes"); _common(watch_parser); watch_parser.add_argument("--interval",type=validate_watch_interval,default=.5)
    return result
def parse_font_roles(values):
    result={}
    for value in values:
        if "=" not in value: raise RendererLabError(f"invalid --font-role value: {value!r}; expected ROLE=PATH")
        role,path=value.split("=",1)
        if not role or not path: raise RendererLabError(f"invalid --font-role value: {value!r}; expected ROLE=PATH")
        if role in result: raise RendererLabError(f"duplicate font role: {role}")
        result[role]=Path(path)
    return result
def build_font_provider(args): return RoleFontProvider(args.font,parse_font_roles(args.font_role))
def _render(exchange, template, output, args, font_provider, *, layout=None):
    payload = fixture(
        args.fixture,
        exchange,
        logo=args.logo,
    )

    return render_preview(
        payload,
        template,
        output,
        font_provider,
        args.debug,
        layout=layout,
    )

def main(argv=None):
    args=parser().parse_args(argv)
    try:
        font_provider=build_font_provider(args)
        if args.command=="preview": result=_render(Exchange(args.exchange),args.template,args.output,args,font_provider); print(f"rendered {result.file_path} {result.width}x{result.height} PNG {result.sha256}")
        elif args.command=="preview-all":
            for exchange in Exchange: _render(exchange,args.root/"templates"/f"{exchange.value}.png",args.root/"output"/f"{exchange.value}-{args.fixture}.png",args,font_provider)
        else:
            exchange=Exchange(args.exchange); layout=Path(__file__).parents[1]/"layouts"/f"{exchange.value}.py"
            current_layout=load_exchange_layout(exchange)
            def watched_render(reload_layout):
                nonlocal current_layout
                if reload_layout: current_layout=load_exchange_layout(exchange,reload=True)
                return _render(exchange,args.template,args.output,args,font_provider,layout=current_layout)
            watch(watched_render,args.template,layout,args.output,args.interval)
        return 0
    except (RendererLabError,KeyError,OSError) as exc: print(f"error: {exc}",file=sys.stderr); return 2
