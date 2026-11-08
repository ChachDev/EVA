"""Run a disposable, explicitly synthetic browser rehearsal; no model or team services needed."""
import asyncio,os,subprocess,sys,tempfile,time,urllib.request,socket
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
def port():
    with socket.socket() as s:s.bind(("127.0.0.1",0));return s.getsockname()[1]
def serve():
    import uvicorn
    sys.path.insert(0,str(ROOT/"backend"))
    from app.config import Settings
    from app.main import create_app
    from app.contracts import Contracts
    contracts=Contracts(ROOT/"contracts")
    async def fixture(req):
        r=contracts.example("evaluation-response");r["conversation_id"]=req["conversation"]["id"];return r
    settings=Settings(seed_demo=True,database_path=Path(sys.argv[3]),cors_origins=(sys.argv[4],))
    server=uvicorn.Server(uvicorn.Config(create_app(settings,fixture),host="127.0.0.1",port=int(sys.argv[2]),access_log=False))
    async def run():
        task=asyncio.create_task(server.serve())
        while not task.done() and not Path(sys.argv[3]).with_suffix(".stop").exists():await asyncio.sleep(.1)
        server.should_exit=True
        await task
    asyncio.run(run())
def main():
    b,f=port(),port();api=f"http://127.0.0.1:{b}";ui=f"http://127.0.0.1:{f}"
    parent=ROOT/"backend/data";parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=parent,prefix="batch-browser-") as tmp:
        processes=[]
        try:
            for args,cwd,env,url in [
                ([sys.executable,__file__,"serve",str(b),str(Path(tmp)/"fixture.sqlite"),ui],ROOT,os.environ,api+"/health"),
                (["node","node_modules/vite/bin/vite.js","--host","127.0.0.1","--strictPort","--port",str(f)],ROOT/"frontend",
                 {**os.environ,"VITE_API_MODE":"http","VITE_API_URL":api},ui)]:
                processes.append(subprocess.Popen(args,cwd=cwd,env=env,stdout=subprocess.DEVNULL,
                    creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0)))
                for _ in range(100):
                    if processes[-1].poll() is not None:raise RuntimeError("Test host failed")
                    try:
                        with urllib.request.urlopen(url,timeout=1):break
                    except OSError:time.sleep(.2)
                else:raise RuntimeError("Test host unavailable")
            subprocess.run(["node",str(ROOT/"ml/integration/batch_ui.mjs"),ui],check=True,timeout=90)
        finally:
            (Path(tmp)/"fixture.stop").touch()
            for i,p in reversed(list(enumerate(processes))):
                if p.poll() is None:
                    if i:p.terminate()
                    p.wait(10)
if __name__=="__main__":
    if sys.argv[1:2]==["serve"]:serve()
    else:main()
