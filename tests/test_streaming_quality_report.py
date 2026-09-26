"""Quality reports consume source objects once without normalizing bad schema."""
import json
from lib.domain import release_quality
from lib.bootstrap.releases import release_application


def test_single_pass_quality_matches_list_and_hashes_once(monkeypatch):
    rows=[{'id':str(i),'messages':[{'role':'user','content':str(i)}, {'role':'assistant','content':'answer'}]} for i in range(1000)]
    rows.append({'id':'bad','messages':'broken'})
    expected=release_quality.report(rows)
    calls=[]
    original=release_quality.sample_hash
    def fingerprint(row):
        calls.append(row['id'])
        return original(row)
    monkeypatch.setattr(release_quality,'sample_hash',fingerprint)
    actual=release_quality.report((row for row in rows))
    assert actual==expected
    assert calls==[row['id'] for row in rows]
    assert actual['issues']==[{'sample':'bad','code':'messages_missing'}]


def test_raw_preview_keeps_invalid_conversation_and_source_identity(tmp_path):
    path=tmp_path/'raw.jsonl'
    rows=[{'id':'bad','messages':'broken'}, {'id':'other','messages':[{'role':'user','content':42}]}]
    path.write_text('\n'.join(json.dumps(row) for row in rows)+'\n',encoding='utf-8')
    preview=release_application().raw_preview_samples(path)
    assert list(preview)==rows and preview[1]==rows[1]
    result=release_quality.report(preview)
    assert [row['code'] for row in result['issues']]==['messages_missing','message_schema']
    assert result['samples']==2


def test_empty_iterator_still_blocks_release():
    result=release_quality.report(iter(()))
    assert result['samples']==0 and 'empty_dataset' in result['block_reasons']
