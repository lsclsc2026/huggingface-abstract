from __future__ import annotations
import sqlite3
import tarfile
import pytest
from pathlib import Path


def test_backup_preserves_wal_database_and_identity_without_raw_wal_files(public_client,tmp_path):
    from deploy.backup import create_backup
    runtime=public_client.app.state.runtime
    # Existing historical records remain covered by private backup.
    from app.comments import Comments
    Comments(runtime.db,runtime.settings.storage.data_dir).create('a','Backup comment','203.0.113.8')
    root=runtime.settings.storage.data_dir
    key=(root/'comment_identity.key').read_bytes()
    output=tmp_path/'backup.tar.gz'
    create_backup(root,output,runtime.settings.path)
    assert output.stat().st_mode & 0o777==0o600
    with tarfile.open(output) as archive:
        names=archive.getnames()
        assert 'data/paper_daily.db' in names and 'config.yaml' in names
        assert not any(n.endswith(('-wal','-shm')) for n in names)
        assert archive.extractfile('data/comment_identity.key').read()==key
        restored=tmp_path/'restored.db';restored.write_bytes(archive.extractfile('data/paper_daily.db').read())
    with sqlite3.connect(restored) as con:
        assert con.execute("select note from papers where id='a'").fetchone()[0]=='PRIVATE NOTE SENTINEL'
        assert con.execute('select content from site_comments').fetchone()[0]=='Backup comment'
        assert con.execute('pragma integrity_check').fetchone()[0]=='ok'
    with pytest.raises(FileExistsError):create_backup(root,output)
    with pytest.raises(ValueError):create_backup(root,root/'recursive.tar.gz')


def test_admin_token_creation_is_private_and_never_overwrites(tmp_path):
    from deploy.admin import init_admin_token
    path=tmp_path/'secrets'/'admin.token'
    init_admin_token(path)
    first=path.read_bytes()
    assert len(first)>=32
    assert path.stat().st_mode & 0o777==0o600
    with pytest.raises(FileExistsError):init_admin_token(path)
    assert path.read_bytes()==first
