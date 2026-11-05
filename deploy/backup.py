#!/usr/bin/env python3
"""Create a private, consistent SQLite backup including stable identity keys."""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import sqlite3
import tarfile
import tempfile


def create_backup(data_dir: Path, output: Path, config: Path | None = None) -> None:
    data_dir=data_dir.resolve();output=output.resolve()
    if output.is_relative_to(data_dir):raise ValueError('备份文件不能写入正在备份的数据目录。')
    if output.exists():raise FileExistsError(output)
    database=data_dir/'paper_daily.db'
    if not database.is_file():raise FileNotFoundError(database)
    if config is not None and not config.is_file():raise FileNotFoundError(config)
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as scratch:
        scratch=Path(scratch);snapshot=scratch/'paper_daily.db'
        source=sqlite3.connect(database.as_uri()+'?mode=ro',uri=True)
        try:
            with sqlite3.connect(snapshot) as dest:source.backup(dest)
        finally:source.close()
        archive=scratch/'backup.tar.gz'
        fd=os.open(archive,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as stream:
            with tarfile.open(fileobj=stream,mode='w:gz') as tar:
                tar.add(snapshot,arcname='data/paper_daily.db')
                for item in sorted(data_dir.iterdir()):
                    if item.name not in {'paper_daily.db','paper_daily.db-wal','paper_daily.db-shm'}:
                        tar.add(item,arcname='data/'+item.name)
                if config is not None:tar.add(config,arcname='config.yaml')
        # Atomic and no-clobber: a failed or concurrent backup cannot replace a
        # known-good archive or expose a partially written target.
        os.link(archive,output)


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir',type=Path,default=Path('data'))
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--config',type=Path)
    args=parser.parse_args()
    create_backup(args.data_dir,args.output,args.config)
    print('Backup created:',args.output)


if __name__=='__main__':main()
