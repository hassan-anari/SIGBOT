import io,tempfile,unittest
from pathlib import Path
from PIL import Image
from sqlalchemy import text
from chainura.domain.destination import Exchange
from chainura.errors import TemplateAssetError,TemplateNotConfiguredError,TemplateValidationError
from chainura.persistence.database import create_session_factory
from chainura.persistence.transaction import SqlAlchemySignalistTransactionFactory
from chainura.rendering.models import EXCHANGE_IMAGE_SPECS
from chainura.rendering.templates import TemplateAssetStore,TemplateService
from integration_tests.support import create_safe_engine,migrate,require_safe_test_database

def png(exchange,color="white"):
    spec=EXCHANGE_IMAGE_SPECS[exchange];stream=io.BytesIO()
    Image.new("RGB",(spec.width,spec.height),color).save(stream,"PNG");return stream.getvalue()

class TemplateIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.safe=require_safe_test_database();migrate("head",self.safe)
        self.engine=create_safe_engine(self.safe)
        async with self.engine.begin() as connection:
            await connection.execute(text("INSERT INTO destinations (id,internal_name,telegram_chat_id,destination_type,access_level,daily_limit) VALUES (1,'d',-1,'channel','private',1)"))
        self.tmp=tempfile.TemporaryDirectory();self.store=TemplateAssetStore(self.tmp.name)
        self.service=TemplateService(SqlAlchemySignalistTransactionFactory(create_session_factory(self.engine)),self.store)
    async def asyncTearDown(self):await self.engine.dispose();self.tmp.cleanup();self.safe.cleanup()
    async def test_install_replace_resolve_fallback_and_remove(self):
        first=await self.service.install(Exchange.OURBIT,png(Exchange.OURBIT,"white"))
        self.assertEqual((await self.service.resolve(Exchange.OURBIT,1)).sha256,first.sha256)
        override=await self.service.install(Exchange.OURBIT,png(Exchange.OURBIT,"black"),destination_id=1)
        self.assertTrue((await self.service.resolve(Exchange.OURBIT,1)).is_destination_specific)
        self.assertEqual((await self.service.resolve(Exchange.OURBIT,1)).sha256,override.sha256)
        await self.service.remove_override(Exchange.OURBIT,1);await self.service.remove_override(Exchange.OURBIT,1)
        self.assertEqual((await self.service.resolve(Exchange.OURBIT,1)).sha256,first.sha256)
        for invalid in (None,0,-1,True,"1"):
            with self.assertRaises(TemplateValidationError):await self.service.remove_override(Exchange.OURBIT,invalid)
        self.assertEqual((await self.service.resolve(Exchange.OURBIT)).sha256,first.sha256)
    async def test_corrupt_override_never_falls_back_and_cascade(self):
        await self.service.install(Exchange.TOOBIT,png(Exchange.TOOBIT))
        override=await self.service.install(Exchange.TOOBIT,png(Exchange.TOOBIT,"black"),destination_id=1)
        (self.store.root/override.storage_key).unlink()
        with self.assertRaises(TemplateAssetError):await self.service.resolve(Exchange.TOOBIT,1)
        async with self.engine.begin() as connection:
            await connection.execute(text("DELETE FROM destinations WHERE id=1"))
            count=await connection.scalar(text("SELECT count(*) FROM signal_templates WHERE destination_id=1"))
        self.assertEqual(count,0)
    async def test_missing_default(self):
        with self.assertRaises(TemplateNotConfiguredError):await self.service.resolve(Exchange.LBANK)
    async def test_weex_default_override_validation_and_fallback(self):
        default=await self.service.install(Exchange.WEEX,png(Exchange.WEEX,"white"))
        self.assertEqual((default.width,default.height),(1000,1000))
        bad=io.BytesIO();Image.new("RGB",(999,1000),"white").save(bad,"PNG")
        with self.assertRaises(TemplateValidationError):
            await self.service.install(Exchange.WEEX,bad.getvalue())
        override=await self.service.install(Exchange.WEEX,png(Exchange.WEEX,"black"),destination_id=1)
        resolved=await self.service.resolve(Exchange.WEEX,1)
        self.assertTrue(resolved.is_destination_specific);self.assertEqual(resolved.sha256,override.sha256)
        await self.service.remove_override(Exchange.WEEX,1)
        resolved=await self.service.resolve(Exchange.WEEX,1)
        self.assertFalse(resolved.is_destination_specific);self.assertEqual(resolved.sha256,default.sha256)
    async def test_all_exchange_default_template_contracts_remain_installable(self):
        for exchange in Exchange:
            asset=await self.service.install(exchange,png(exchange))
            self.assertEqual((asset.width,asset.height),
                             (EXCHANGE_IMAGE_SPECS[exchange].width,EXCHANGE_IMAGE_SPECS[exchange].height))
