import asyncio
import unittest
from chainura.application.publication import SignalDetailsPublicationWorker

class WorkerIsolationTests(unittest.IsolatedAsyncioTestCase):
 async def test_unexpected_failure_is_recovered_and_other_item_continues(self):
  class Service:
   def __init__(self):self.processed=[];self.recovered=[];self.sweeps=0
   async def recover(self):pass
   async def pending_ids(self):
    self.sweeps+=1
    return (1,2) if self.sweeps==1 else ()
   async def process(self,item):
    self.processed.append(item)
    if item==1:raise RuntimeError('boom')
   async def recover_unexpected_failure(self,item):self.recovered.append(item)
  service=Service();worker=SignalDetailsPublicationWorker(service,concurrency=2,sweep_seconds=.1)
  await worker.start()
  for _ in range(20):
   if service.recovered:break
   await asyncio.sleep(.01)
  await worker.close()
  self.assertEqual(service.processed,[1,2]);self.assertEqual(service.recovered,[1])
