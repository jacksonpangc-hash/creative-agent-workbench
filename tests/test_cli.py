import json
from creative_agent.cli import main


def test_cli_and_api_share_store_and_ingest(tmp_path,capsys):
    assert main(['--data-dir',str(tmp_path),'project','create','--name','共享项目','--budget','10'])==0
    saved=json.loads(capsys.readouterr().out)
    assert saved['name']=='共享项目'
    assert main(['--data-dir',str(tmp_path),'project','list'])==0
    assert json.loads(capsys.readouterr().out)[0]['id']==saved['id']
