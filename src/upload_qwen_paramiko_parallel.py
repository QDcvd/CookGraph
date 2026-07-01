import argparse
import hashlib
import math
import os
import posixpath
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import paramiko


def ssh_client(host, user, password):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=host,
        username=user,
        password=password,
        timeout=20,
        banner_timeout=20,
        auth_timeout=20,
        look_for_keys=False,
        allow_agent=False,
    )
    return client


def run(client, command):
    stdin, stdout, stderr = client.exec_command(command)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    code = stdout.channel.recv_exit_status()
    if code != 0:
        raise RuntimeError(f"remote command failed ({code}): {command}\n{err}")
    return out


def remote_size(sftp, path):
    try:
        return sftp.stat(path).st_size
    except FileNotFoundError:
        return 0
    except OSError:
        return 0


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def upload_part(args, index, offset, length, progress, lock):
    part_name = f"{args.model_name}.part.{index:05d}"
    remote_part = posixpath.join(args.remote_dir, "chunks", part_name)

    client = ssh_client(args.host, args.user, args.password)
    sftp = client.open_sftp()
    try:
        done = remote_size(sftp, remote_part)
        if done > length:
            sftp.remove(remote_part)
            done = 0
        if done == length:
            with lock:
                progress[index] = length
            return part_name, "skip"

        mode = "ab" if done else "wb"
        with open(args.src, "rb") as local, sftp.open(remote_part, mode) as remote:
            local.seek(offset + done)
            remaining = length - done
            sent = done
            while remaining:
                chunk = local.read(min(args.buffer_size, remaining))
                if not chunk:
                    raise IOError(f"unexpected EOF in local file for {part_name}")
                remote.write(chunk)
                sent += len(chunk)
                remaining -= len(chunk)
                with lock:
                    progress[index] = sent
        return part_name, "uploaded"
    finally:
        sftp.close()
        client.close()


def progress_printer(total_size, progress, lock, stop_event):
    start = time.time()
    while not stop_event.wait(10):
        with lock:
            done = sum(progress.values())
        elapsed = max(time.time() - start, 1)
        speed = done / elapsed / (1024 * 1024)
        pct = done * 100 / total_size
        print(f"[progress] {done}/{total_size} bytes {pct:.2f}% at {speed:.2f} MiB/s", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--src", default=r"C:\Users\jinhenghao\Downloads\Qwen3.6-27B-Q4_K_M.gguf")
    parser.add_argument("--host", default="100.101.72.21")
    parser.add_argument("--user", default="ubuntu")
    parser.add_argument("--password", default="123456")
    parser.add_argument("--remote-dir", default="/home/ubuntu/.lmstudio/models/lmstudio-community/Qwen3.6-27B-GGUF")
    parser.add_argument("--model-name", default="Qwen3.6-27B-Q4_K_M.gguf")
    parser.add_argument("--part-size", type=int, default=512 * 1024 * 1024)
    parser.add_argument("--parallel", type=int, default=6)
    parser.add_argument("--buffer-size", type=int, default=4 * 1024 * 1024)
    args = parser.parse_args()

    if not os.path.isfile(args.src):
        raise SystemExit(f"source file not found: {args.src}")

    total_size = os.path.getsize(args.src)
    part_count = math.ceil(total_size / args.part_size)
    print(f"source: {args.src}")
    print(f"size: {total_size} bytes")
    print(f"parts: {part_count}, part_size: {args.part_size}, parallel: {args.parallel}")

    print("computing local sha256...")
    digest = sha256_file(args.src)
    print(f"sha256: {digest}")

    setup = ssh_client(args.host, args.user, args.password)
    try:
        run(setup, f"mkdir -p {args.remote_dir!r}/chunks")
        run(setup, f"printf '%s  %s\\n' {digest!r} {args.model_name!r} > {posixpath.join(args.remote_dir, 'chunks', 'SHA256SUMS')!r}")
    finally:
        setup.close()

    progress = {}
    lock = threading.Lock()
    jobs = []
    for i in range(part_count):
        offset = i * args.part_size
        length = min(args.part_size, total_size - offset)
        progress[i] = 0
        jobs.append((i, offset, length))

    stop_event = threading.Event()
    printer = threading.Thread(target=progress_printer, args=(total_size, progress, lock, stop_event), daemon=True)
    printer.start()

    try:
        with ThreadPoolExecutor(max_workers=args.parallel) as pool:
            futures = [pool.submit(upload_part, args, i, offset, length, progress, lock) for i, offset, length in jobs]
            for future in as_completed(futures):
                part_name, status = future.result()
                print(f"[{status}] {part_name}", flush=True)
    finally:
        stop_event.set()
        printer.join(timeout=2)

    print("merging and verifying on remote...")
    merge = ssh_client(args.host, args.user, args.password)
    try:
        command = (
            f"set -e; cd {args.remote_dir!r}; "
            f"cat chunks/{args.model_name}.part.* > {args.model_name!r}; "
            f"sha256sum -c chunks/SHA256SUMS"
        )
        print(run(merge, command))
    finally:
        merge.close()

    print(f"done: {args.user}@{args.host}:{args.remote_dir}/{args.model_name}")


if __name__ == "__main__":
    main()
