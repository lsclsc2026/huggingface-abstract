"""Comments are out of scope even when a stale deployment flag is enabled."""
import pytest


@pytest.mark.parametrize('method,path',[
    ('get','/api/papers/a/comments'),
    ('post','/api/papers/a/comments'),
    ('delete','/api/comments/legacy-comment'),
])
def test_comment_routes_are_absent(public_client,method,path):
    response=getattr(public_client,method)(path,headers={'Authorization':'Bearer test-admin-secret'})
    assert response.status_code==404


def test_public_metadata_and_frontend_have_no_comment_feature(public_client):
    assert 'comments' not in public_client.get('/api/meta').json()
    script=public_client.get('/static/app.js').text
    assert 'commentsShell' not in script
    assert 'loadComments' not in script
    assert 'comment-form' not in script


def test_existing_private_data_survives_feature_removal(public_client):
    paper=public_client.app.state.runtime.db.get_paper('a')
    assert paper['note']=='PRIVATE NOTE SENTINEL'
    assert paper['bookmarked']
    assert public_client.get('/api/papers/a').json()['paper']['analysis']['summary']=='Public explanation'
