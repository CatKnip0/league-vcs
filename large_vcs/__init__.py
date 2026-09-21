import glob
import hashlib
import json
import os
import shutil
import signal
import stat
import zlib

from tqdm import tqdm
from multiprocessing import Pool

BLOCK_SIZE = 65536


def initializer():
    signal.signal(signal.SIGINT, signal.SIG_IGN)


def hash_file(fp):
    file_hash = hashlib.sha256()
    with open(fp, 'rb') as f:
        fb = f.read(BLOCK_SIZE)
        while len(fb) > 0:
            file_hash.update(fb)
            fb = f.read(BLOCK_SIZE)
    return file_hash.hexdigest()


def save_to_repo(src, dst):
    if os.path.exists(dst):
        return
    tmp = dst + '.tmp'
    try:
        shutil.copyfile(src, tmp)
        os.replace(tmp, dst)
        os.chmod(dst, stat.S_IREAD)
    except (PermissionError, FileExistsError):
        try:
            os.unlink(tmp)
        except OSError:
            pass


def load_from_repo(src, dst):
    try:
        os.link(src, dst)
    except OSError:
        shutil.copyfile(src, dst)


class LargeVCS:
    def __init__(self, root, repo_name='lvcs', current_name='current', do_copy=False):
        self.root = os.path.abspath(root)
        self.repo_name = repo_name
        self.current_name = current_name
        self.do_copy = do_copy
        self.current_patch_path = self.repo_path('current.json')

    def path(self, *parts):
        return os.path.abspath(os.path.join(self.root, *parts))

    def repo_path(self, *parts):
        return self.path(self.repo_name, *parts)

    def current_path(self, *parts):
        return self.path(self.current_name, *parts)

    def ensure_repo(self):
        assert os.path.exists(self.path(self.repo_name)), 'Not in repository!'

    def get_patch(self, tag):
        patch_path = self.repo_path('patches', tag + '.json')
        try:
            with open(patch_path) as patch_file:
                return json.load(patch_file)
        except FileNotFoundError:
            return None

    def current(self):
        try:
            with open(self.current_patch_path) as file:
                return json.load(file)
        except FileNotFoundError:
            return None

    def clean(self):
        if not os.path.exists(self.repo_path('current.json')):
            return

        for root, dirs, files in os.walk(self.current_path()):
            for file in files:
                os.chmod(os.path.join(root, file), stat.S_IWRITE)
        try:
            shutil.rmtree(self.current_path())
        except FileNotFoundError:
            pass

        for file in os.listdir(self.repo_path('files')):
            os.chmod(self.repo_path('files', file), stat.S_IREAD)

        os.unlink(self.repo_path('current.json'))

    @classmethod
    def init(cls, root):
        repo = cls(root)
        assert not os.path.exists(repo.repo_path()), f'Repo already exists at {repo.root}'
        os.makedirs(repo.repo_path('files'), exist_ok=False)
        os.makedirs(repo.repo_path('patches'), exist_ok=False)
        return repo

    @classmethod
    def load_or_create(cls, root):
        try:
            return cls.init(root)
        except (AssertionError, Exception):
            return cls(root)

    @staticmethod
    def _hash_file(params):
        full_path, rel_path = params
        return full_path, rel_path, hash_file(full_path)

    @staticmethod
    def _add_file(params):
        full_path, rel_path, checksum, dest_path = params
        save_to_repo(full_path, dest_path)

    def add(self, target, tag):
        self.ensure_repo()
        patch_path = self.repo_path('patches', tag + '.json')
        assert not os.path.exists(patch_path), f'Patch {tag} already exists!'

        all_files = []
        print('[1/3] Retrieving file listing...')
        for root, _, files in os.walk(target):
            for file in files:
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, target)
                all_files.append((full_path, rel_path))

        pool = Pool(4, initializer=initializer)
        try:
            print('[2/3] Hashing files...')
            with_hash = list(tqdm(pool.imap_unordered(self._hash_file, all_files), total=len(all_files)))

            to_add = []
            for full_path, rel_path, checksum in with_hash:
                dest_path = self.repo_path('files', checksum)
                if not os.path.exists(dest_path):
                    to_add.append((full_path, rel_path, checksum, dest_path))

            print('[3/3] Adding files...')
            list(tqdm(pool.imap_unordered(self._add_file, to_add), total=len(to_add)))
        except KeyboardInterrupt:
            pool.terminate()
            pool.join()
            raise
        finally:
            pool.close()

        patch = {checksum: rel_path for _, rel_path, checksum in with_hash}
        with open(patch_path, 'w') as patch_file:
            json.dump(patch, patch_file)

    def list(self):
        files = glob.glob(self.repo_path('patches', '*.json'))
        return list(sorted(
            os.path.splitext(os.path.basename(fp))[0] for fp in files
        ))

    def drop(self, tag):
        self.ensure_repo()
        assert self.current() != tag, f"Can't delete patch {tag} as it's the current one."
        patch_path = self.repo_path('patches', tag + '.json')
        assert os.path.exists(patch_path), f'Patch {tag} does not exist!'

        with open(patch_path) as patch_file:
            patch = json.load(patch_file)
        os.unlink(patch_path)

        to_remove = set(patch.keys())
        for file_path in glob.glob(self.repo_path('patches', '*.json')):
            with open(file_path) as pf:
                other_patch = json.load(pf)
            to_remove.difference_update(set(other_patch.keys()))
            if not to_remove:
                break

        for checksum in tqdm(to_remove, desc='Removing files'):
            checksum_path = self.repo_path('files', checksum)
            os.chmod(checksum_path, stat.S_IWRITE)
            os.unlink(checksum_path)

    def _restore_file(self, params):
        checksum, rel_path = params
        checksum_path = self.repo_path('files', checksum)
        full_path = self.current_path(rel_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        load_from_repo(checksum_path, full_path)

    def restore(self, tag, clean=False):
        self.ensure_repo()
        patch_path = self.repo_path('patches', tag + '.json')
        assert os.path.exists(patch_path), f'Patch {tag} does not exist!'
        current = self.current()

        if not clean and tag == current:
            print(f'Already on {tag}.')
            return

        with open(patch_path) as patch_file:
            patch = json.load(patch_file)
        new_keys = set(patch.keys())

        add, delete, set_read_only = new_keys, set(), set()
        if clean:
            self.clean()
        elif current:
            with open(self.repo_path('patches', current + '.json')) as f:
                current_patch = json.load(f)
            current_keys = set(current_patch.keys())
            add = new_keys.difference(current_keys)
            set_read_only = current_keys.difference(new_keys)
            delete = [current_patch[checksum] for checksum in set_read_only]

        add = [(checksum, patch[checksum]) for checksum in add]
        add_step = 2 if delete else 1

        if delete:
            print('[1/2] Unlinking old files...')
            for rel_path in tqdm(delete):
                full_path = self.current_path(rel_path)
                dir_path = os.path.dirname(full_path)
                os.chmod(full_path, stat.S_IWRITE)
                os.unlink(full_path)
                try:
                    os.rmdir(dir_path)
                except OSError:
                    pass

            for checksum in set_read_only:
                os.chmod(self.repo_path('files', checksum), stat.S_IREAD)

        print(f'[{add_step}/{add_step}] Linking new files...')
        pool = Pool(4, initializer=initializer)
        try:
            list(tqdm(pool.imap_unordered(self._restore_file, add), total=len(add)))
        except KeyboardInterrupt:
            pool.terminate()
            pool.join()
            raise
        finally:
            pool.close()

        with open(self.current_patch_path, 'w') as file:
            json.dump(tag, file)

        print(f'Restored patch {tag}!')

    def export(self, tag, destination):
        self.ensure_repo()
        patch_path = self.repo_path('patches', tag + '.json')
        assert os.path.exists(patch_path), f'Patch {tag} does not exist!'

        with open(patch_path) as patch_file:
            patch = json.load(patch_file)

        for checksum, rel_path in tqdm(patch.items(), desc=f'Exporting {tag}'):
            src = self.repo_path('files', checksum)
            dst = os.path.join(destination, rel_path)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(src, dst)

    def wipe(self):
        for file in os.listdir(self.repo_path('files')):
            os.chmod(self.repo_path('files', file), stat.S_IWRITE)
        shutil.rmtree(self.root)
