from __future__ import annotations
import hashlib, inspect
import io
import os
from dataclasses import FrozenInstanceError, replace
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from argparse import Namespace
from PIL import Image

from chainura.domain.destination import Exchange
from chainura.domain.signal import TradeDirection
from chainura.rendering.lab.fixtures import FIXTURES, fixture
from chainura.rendering.lab.cli import build_font_provider, parse_font_roles
from chainura.rendering.lab.preview import (RENDERERS, render_lbank_initial,
    render_ourbit_initial, render_preview, render_toobit_initial)
from chainura.rendering.lab.watch import validate_watch_interval, watch
from chainura.rendering.layouts.models import FontSpec, InitialCardLayout, Rectangle, TextLayer
from chainura.rendering.layouts.ourbit import LAYOUT
from chainura.rendering.models import EXCHANGE_IMAGE_SPECS
from chainura.rendering.pillow.compositor import compose_initial
from chainura.rendering.pillow.errors import RendererAssetError, RendererLabError, RendererLayoutError
from chainura.rendering.pillow.formatting import (format_decimal, format_elapsed,
    format_initial_pnl, format_tehran_timestamp, format_utc_timestamp)
from chainura.rendering.pillow.text import DefaultTestFontProvider, PathFontProvider, RoleFontProvider

class RendererLabTests(unittest.TestCase):
    def setUp(self):
        self.directory=TemporaryDirectory(); self.root=Path(self.directory.name); self.font=DefaultTestFontProvider()
    def tearDown(self): self.directory.cleanup()
    def template(self,exchange,size=None):
        spec=EXCHANGE_IMAGE_SPECS[exchange]; path=self.root/f"{exchange.value}.png"
        Image.new("RGBA",size or (spec.width,spec.height),(20,30,40,255)).save(path,"PNG"); return path
    def render(self,exchange,**kwargs):
        output=kwargs.pop("output",self.root/f"out-{exchange.value}.png")
        return render_preview(fixture(kwargs.pop("name","composite-long"),exchange,kwargs.pop("logo",None)),self.template(exchange),output,self.font,kwargs.pop("debug",False))

    def test_all_exchange_dimensions_png_hash_and_output_creation(self):
        for exchange in Exchange:
            with self.subTest(exchange=exchange):
                result=self.render(exchange,output=self.root/"nested"/f"{exchange.value}.png")
                self.assertEqual((result.width,result.height),(EXCHANGE_IMAGE_SPECS[exchange].width,EXCHANGE_IMAGE_SPECS[exchange].height))
                self.assertEqual(result.content_type,"image/png"); data=result.file_path.read_bytes()
                self.assertEqual(data[:8],b"\x89PNG\r\n\x1a\n"); self.assertEqual(result.sha256,hashlib.sha256(data).hexdigest())

    def test_deterministic_and_debug_changes_only_debug_output(self):
        first=self.render(Exchange.OURBIT,output=self.root/"a.png")
        second=self.render(Exchange.OURBIT,output=self.root/"b.png")
        debug=self.render(Exchange.OURBIT,output=self.root/"debug.png",debug=True)
        self.assertEqual(first.sha256,second.sha256); self.assertNotEqual(first.sha256,debug.sha256)

    def test_template_errors_and_atomic_existing_output(self):
        with self.assertRaises(RendererAssetError):
            render_preview(fixture("composite-long",Exchange.OURBIT),self.root/"missing.png",self.root/"x.png",self.font)
        bad=self.template(Exchange.OURBIT,(10,10)); output=self.root/"existing.png"; output.write_bytes(b"original")
        with self.assertRaises(RendererAssetError): render_preview(fixture("composite-long",Exchange.OURBIT),bad,output,self.font)
        self.assertEqual(output.read_bytes(),b"original")

    def test_missing_font(self):
        with self.assertRaises(RendererAssetError): PathFontProvider(self.root/"missing.ttf")

    def test_role_font_fallback_override_and_cache(self):
        fallback=self.root/"fallback.ttf"; fallback.touch()
        medium=self.root/"medium.otf"; medium.touch()
        shared=self.root/"shared.ttf"; shared.touch()
        created=[]
        def load(path,size):
            value=object(); created.append((Path(path),size,value)); return value
        with patch("chainura.rendering.pillow.text.ImageFont.truetype",side_effect=load):
            provider=RoleFontProvider(fallback,{"medium":medium,"semibold":shared,"bold":shared})
            regular=provider.get("regular",24); medium_font=provider.get("medium",24)
            semibold=provider.get("semibold",24); bold=provider.get("bold",24)
            self.assertIs(regular,provider.get("regular",24))
            self.assertIs(semibold,bold)
        self.assertEqual(created.count((fallback.resolve(),24,regular)),1)
        self.assertEqual(created.count((medium.resolve(),24,medium_font)),1)
        self.assertEqual(sum(path==shared.resolve() and size==24 for path,size,_ in created),1)
        self.assertEqual(provider.role_paths["medium"],medium.resolve())

    def test_all_roles_resolve_to_explicit_paths_and_missing_role_is_controlled(self):
        paths={role:self.root/f"{role}.ttf" for role in ("regular","medium","semibold","bold")}
        for path in paths.values(): path.touch()
        with patch("chainura.rendering.pillow.text.ImageFont.truetype",side_effect=lambda path,size:(Path(path),size)):
            provider=RoleFontProvider(role_paths=paths)
            self.assertEqual({role:provider.get(role,20)[0] for role in paths},{role:path.resolve() for role,path in paths.items()})
            with self.assertRaises(RendererAssetError): provider.get("unknown",20)

    def test_font_role_cli_validation_and_assets(self):
        self.assertEqual(parse_font_roles(["bold=/tmp/bold.ttf"]),{"bold":Path("/tmp/bold.ttf")})
        for values in (["broken"],["=/tmp/font.ttf"],["bold="],["bold=a","bold=b"]):
            with self.subTest(values=values),self.assertRaises(RendererLabError): parse_font_roles(values)
        with self.assertRaises(RendererAssetError): build_font_provider(Namespace(font=None,font_role=[]))
        with self.assertRaises(RendererAssetError): RoleFontProvider(role_paths={"bold":self.root/"missing.ttf"})
        invalid=self.root/"invalid.ttf"; invalid.write_text("not a font")
        with self.assertRaises(RendererAssetError): RoleFontProvider(fallback_path=invalid)

    def test_layouts_request_calibration_roles(self):
        from chainura.rendering.layouts.lbank import LAYOUT as lbank
        from chainura.rendering.layouts.toobit import LAYOUT as toobit
        expected={"symbol":"bold","direction":"semibold","leverage":"semibold","entry":"medium","price":"medium","pnl":"semibold","time":"regular"}
        for layout in (LAYOUT,toobit,lbank): self.assertEqual({layer.field:layer.font.role for layer in layout.text_layers},expected)

    def test_decimal_and_approved_initial_formats(self):
        self.assertEqual(format_decimal(Decimal("0.0000000012300")),"0.0000000012300")
        self.assertEqual(format_initial_pnl(Decimal("0.00")),"0.00%")
        self.assertEqual(format_elapsed(0),"00H 00M")
        value=fixture("composite-long",Exchange.TOOBIT).rendered_at
        self.assertEqual(format_tehran_timestamp(value),"2026-08-01 16:04:56")
        self.assertEqual(format_utc_timestamp(value),"2026-08-01 12:34:56 UTC")

    def test_direction_long_short_symbol_fit_and_high_leverage(self):
        for name,direction in (("composite-long",TradeDirection.LONG),("composite-short",TradeDirection.SHORT),("long-symbol",TradeDirection.LONG),("high-leverage",TradeDirection.LONG)):
            self.assertIs(fixture(name,Exchange.LBANK).direction,direction); self.render(Exchange.LBANK,name=name)

    def test_controlled_minimum_size_failure(self):
        layout=InitialCardLayout(Exchange.OURBIT,(TextLayer("symbol",Rectangle(0,0,1,1),FontSpec("main",10,10)),))
        with self.assertRaises(RendererLayoutError): compose_initial(fixture("long-symbol",Exchange.OURBIT),self.template(Exchange.OURBIT),self.root/"fail.png",layout,self.font)

    def test_optional_logo_absent_present_and_invalid(self):
        self.render(Exchange.TOOBIT)
        logo=self.root/"logo.png"; Image.new("RGBA",(200,100),(255,0,0,128)).save(logo,"PNG")
        self.render(Exchange.TOOBIT,logo=logo,output=self.root/"logo-out.png")
        with self.assertRaises(RendererAssetError): self.render(Exchange.TOOBIT,logo=self.root/"none.png")

    def test_fixture_and_layout_immutability(self):
        self.assertEqual(fixture("small-price",Exchange.OURBIT),fixture("small-price",Exchange.OURBIT))
        self.assertEqual(len(FIXTURES),7)
        with self.assertRaises(FrozenInstanceError): LAYOUT.exchange="changed"

    def test_no_clock_or_production_registration(self):
        import chainura.rendering.pillow.compositor as compositor
        source=inspect.getsource(compositor)
        self.assertNotIn("datetime.now",source); self.assertNotIn("time.time",source)
        self.assertEqual(set(RENDERERS),set(Exchange))

    def test_watch_hot_reload_syntax_recovery_and_clean_interrupt(self):
        template=self.root/"template.png"; template.write_text("template-1")
        layout=self.root/"layout.py"; layout.write_text("COORDINATE = 10\n")
        output=self.root/"output.png"; effective=[]; reloads=[]; tick=100

        def set_file(path,content):
            nonlocal tick
            tick+=1; path.write_text(content); os.utime(path,ns=(tick,tick))
        def render(reload_layout):
            reloads.append(reload_layout)
            namespace={}; exec(compile(layout.read_text(),str(layout),"exec"),namespace)
            effective.append(namespace["COORDINATE"]); output.write_text(str(effective[-1]))
        events=[
            lambda:set_file(template,"template-2"),
            lambda:set_file(layout,"COORDINATE =\n"),
            lambda:set_file(layout,"COORDINATE = 30\n"),
            KeyboardInterrupt(),
        ]
        def sleep(_interval):
            event=events.pop(0)
            if isinstance(event,BaseException): raise event
            event()
        printed=io.StringIO()
        with patch("chainura.rendering.lab.watch.time.sleep",side_effect=sleep),patch("sys.stdout",printed):
            watch(render,template,layout,output,.01)
        self.assertEqual(effective,[10,10,30])
        self.assertEqual(reloads,[False,False,True,True])
        self.assertIn("render error:",printed.getvalue())
        self.assertIn("watch stopped",printed.getvalue())
        self.assertEqual(output.read_text(),"30")

    def test_invalid_watch_intervals(self):
        for value in (0,-1,float("nan"),float("inf"),float("-inf")):
            with self.subTest(value=value),self.assertRaises(ValueError): validate_watch_interval(value)
        self.assertEqual(validate_watch_interval(.25),.25)

    def test_explicit_renderers_reject_other_exchanges_without_output_changes(self):
        cases=((render_ourbit_initial,Exchange.OURBIT),(render_toobit_initial,Exchange.TOOBIT),(render_lbank_initial,Exchange.LBANK))
        for renderer,expected in cases:
            for actual in Exchange:
                if actual is expected: continue
                with self.subTest(renderer=expected,actual=actual):
                    absent=self.root/f"{expected.value}-{actual.value}-absent.png"
                    with self.assertRaises(RendererLayoutError): renderer(fixture("composite-long",actual),self.root/"missing.png",absent,self.font)
                    self.assertFalse(absent.exists())
                    existing=self.root/f"{expected.value}-{actual.value}-existing.png"; existing.write_bytes(b"unchanged")
                    with self.assertRaises(RendererLayoutError): renderer(fixture("composite-long",actual),self.root/"missing.png",existing,self.font)
                    self.assertEqual(existing.read_bytes(),b"unchanged")

if __name__=="__main__": unittest.main()
