"""Repeat scheduling with a logical clock and execute one real local tool loop."""
from pathlib import Path
import json,tempfile,time
from .work_queue import WorkQueue,replay
from .runtime import run_ticket
from .providers import Replay
from .domain import Scope,OrderReader
def experiment():
 with tempfile.TemporaryDirectory() as d:
  q=replay(Path(d)/'logical.sqlite3')
  real=WorkQueue(Path(d)/'real.sqlite3')
  real.enqueue(dict(tenant='tenant-demo',brand='demo-brand',store='store-001',topic='fixture-topic',version=1,created=time.time(),priority=0,service='checkout',stage='paid',error_code='NO_ACK',deployment='release-demo',campaign='none'))
  def worker(job):
   result=run_ticket('支付流水 P1001，客户说没有出单',Scope(job['brand'],job['store']),Replay(),OrderReader())
   result['usage']=None
   return result
  actual=real.execute_one(worker)
  result={'synthetic':True,'scheduling':{'mode':q['mode'],'clock_is_simulated':True,'dispatch_order':q['dispatch_order'],'candidate_groups':q['candidates'],'queue_wait_ms':[e['detail']['queue_wait_ms'] for e in q['state']['events'] if e['kind']=='claimed']},'actual_worker':{'mode':'scripted_replay_real_execution','disposition':actual['disposition'],'status':actual['result']['status'],'evidence_count':len(actual['result']['evidence']),'trace':actual['result']['trace'],'usage':actual['result']['usage'],'queue_worker_elapsed_ms':actual['result']['queue_worker_elapsed_ms']},'limitations':['调度实验使用逻辑时钟，不是负载基准','实际工作者调用固定 Replay 和本地订单工具，不调用模型或飞书']}
  return result

if __name__=='__main__':
 print(json.dumps(experiment(),ensure_ascii=False,indent=2))
