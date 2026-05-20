#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
/opt/ai/oroma/v2.11/mini_programs/hide_seek.py

Grid-based Hide & Seek:
- 1 Seeker vs 4 Hiders
- simple line-of-sight + heatmap hints
- headless env + simple loop for smoke-tests
"""

import random
from typing import Dict, List, Tuple

W, H = 15, 10
N_HIDERS = 4
WALL_RATE = 0.10

EMPTY, WALL, SEEKER, HIDER = 0, 1, 2, 3

Vec = Tuple[int,int]

def in_bounds(x: int, y: int) -> bool:
    return 0 <= x < W and 0 <= y < H

def los(a: Vec, b: Vec, grid) -> bool:
    # Bresenham-ish line of sight, stop at WALL
    x0,y0 = a; x1,y1 = b
    dx = 1 if x1>x0 else -1 if x1<x0 else 0
    dy = 1 if y1>y0 else -1 if y1<y0 else 0
    x,y = x0,y0
    while (x,y)!=(x1,y1):
        x += dx; y += dy
        if not in_bounds(x,y): return False
        if grid[y][x] == WALL: return False
    return True

def gen_world(seed: int = 0) -> Dict:
    rng = random.Random(seed)
    grid = [[EMPTY for _ in range(W)] for _ in range(H)]
    # random walls
    for y in range(H):
        for x in range(W):
            if rng.random() < WALL_RATE:
                grid[y][x] = WALL
    # place seeker
    while True:
        sx,sy = rng.randrange(W), rng.randrange(H)
        if grid[sy][sx]==EMPTY:
            grid[sy][sx]=SEEKER
            seeker=(sx,sy)
            break
    # place hiders
    hiders: List[Vec] = []
    while len(hiders) < N_HIDERS:
        x,y = rng.randrange(W), rng.randrange(H)
        if grid[y][x]==EMPTY:
            grid[y][x]=HIDER
            hiders.append((x,y))
    return {"grid":grid,"seeker":seeker,"hiders":hiders,"steps":0,"found":0}

def neighbors(p: Vec) -> List[Vec]:
    x,y = p
    out = [(x+1,y),(x-1,y),(x,y+1),(x,y-1)]
    return [(a,b) for a,b in out if in_bounds(a,b)]

def step(env: Dict) -> Dict:
    grid = env["grid"]
    sx,sy = env["seeker"]
    hs = env["hiders"][:]

    # seeker moves towards closest visible or random walk
    target = None
    vis_h = [h for h in hs if los((sx,sy), h, grid)]
    if vis_h:
        target = min(vis_h, key=lambda h: abs(h[0]-sx)+abs(h[1]-sy))
    moves = neighbors((sx,sy))
    random.shuffle(moves)
    best = (sx,sy)
    if target:
        # greedy
        bx,by = best
        dist = abs(target[0]-sx)+abs(target[1]-sy)
        for mx,my in moves:
            if grid[my][mx]==EMPTY:
                d = abs(target[0]-mx)+abs(target[1]-my)
                if d < dist:
                    dist = d; bx,by = mx,my
        best = (bx,by)
    else:
        for mx,my in moves:
            if grid[my][mx]==EMPTY:
                best = (mx,my); break

    # move seeker
    grid[sy][sx]=EMPTY
    sx,sy = best
    if grid[sy][sx]==HIDER:
        # found
        env["found"] += 1
        # remove hider
        env["hiders"] = [h for h in hs if h!=(sx,sy)]
    grid[sy][sx]=SEEKER
    env["seeker"]=(sx,sy)

    # hiders move away if visible, else random stay/move
    new_hiders: List[Vec] = []
    for (hx,hy) in hs:
        if (hx,hy) not in env["hiders"]:
            continue  # was found
        grid[hy][hx]=EMPTY
        cand = neighbors((hx,hy))
        cand = [c for c in cand if grid[c[1]][c[0]]==EMPTY]
        if los((sx,sy),(hx,hy),grid) and cand:
            # run away: choose farther
            cand.sort(key=lambda c: - (abs(sx-c[0])+abs(sy-c[1])) )
            nx,ny = cand[0]
        elif cand and random.random()<0.5:
            nx,ny = random.choice(cand)
        else:
            nx,ny = hx,hy
        grid[ny][nx]=HIDER
        new_hiders.append((nx,ny))
    env["hiders"]=new_hiders
    env["steps"] += 1
    return env

def render_ascii(env: Dict) -> str:
    chars = {EMPTY:" .", WALL:"##", SEEKER:" S", HIDER:" H"}
    rows = []
    for y in range(H):
        row = ""
        for x in range(W):
            row += chars[ env["grid"][y][x] ]
        rows.append(row)
    return "\n".join(rows)

if __name__ == "__main__":
    env = gen_world(seed=0)
    for _ in range(50):
        env = step(env)
    print(render_ascii(env))
    print("found:", env["found"], "steps:", env["steps"], "remaining:", len(env["hiders"]))
