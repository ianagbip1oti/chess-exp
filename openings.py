import collections
import sys
import functools
import requests
import queue
import chess
import chess.engine
import chess.pgn
import logging
import time

logging.basicConfig(level=logging.INFO)

# 14 = 14/2 = 7 for black, 8 for white
MAX_PLY = 14

engine = chess.engine.SimpleEngine.popen_uci("/usr/bin/stockfish")


def winning(board, pov):
    score = engine.analyse(board, chess.engine.Limit(depth=15))
    return score["score"].pov(pov)


# Choose most played moves (same logic as opposition moves we consider)
# Choose the one with the highest winning pct
def lichess_winrate(board, pov):
    return winrate(board, pov, get_moves_table_fen)


def masters_winrate(board, pov):
    return winrate(board, pov, get_masters_table_fen)


def winrate(board, pov, get_moves_table):
    min_pct = 0.05
    min_moves = 2

    board_copy = board.copy()
    move = board_copy.pop()

    r = get_moves_table(board_copy.fen())

    total_moves = r["white"] + r["black"] + r["draws"]

    if total_moves < 100:
        return (
            winning(board, pov)
            .wdl(model="lichess", ply=board_copy.ply())
            .winning_chance()
        )

    table = {}

    for m in r["moves"]:
        count = m["white"] + m["black"] + m["draws"]
        wins = m["white"] if pov == chess.WHITE else m["black"]

        table[chess.Move.from_uci(m["uci"])] = (
            count / total_moves,
            count,
            wins / count,
        )

    candidates = [
        k for k in table.keys() if table[k][0] > min_pct or table[k][1] > 1_000_000
    ]

    if len(candidates) < min_moves:
        candidates = sorted(table.keys(), key=lambda k: -table[k][0])[:min_moves]

    if move in candidates and table[move][1] > 20:
        return table[move][2]
    elif move in candidates:
        return (
            winning(board, pov)
            .wdl(model="lichess", ply=board_copy.ply())
            .winning_chance()
        )
    else:
        return 0


def find_best_move(board, heuristic):
    moves = []

    for m in board.legal_moves:
        board_copy = board.copy()
        board_copy.push(m)
        moves.append((m, heuristic(board_copy, board.turn)))

    return sorted(moves, key=lambda x: -x[1])[0][0]


@functools.cache
def get_moves_table_fen(fen):
    params = {
        "fen": fen,
        "moves": 15,
        "topGames": 0,
        "recentGames": 0,
        "variant": "standard",
        "speeds[]": ["blitz", "rapid", "classical"],
        "ratings[]": [1600, 1800, 2000, 2200],
    }

    try:
        rsp = requests.get("https://explorer.lichess.ovh/lichess", params=params)
        if rsp.status_code == 429:
            logging.info("Pausing for rate limit...")
            time.sleep(60)
            rsp = requests.get("https://explorer.lichess.ovh/lichess", params=params)

        return rsp.json()
    except:
        logging.warning("response: %s", rsp)


@functools.cache
def get_masters_table_fen(fen):
    params = {
        "fen": fen,
        "moves": 15,
        "topGames": 0,
        "recentGames": 0,
        "variant": "standard",
    }

    try:
        rsp = requests.get("https://explorer.lichess.ovh/master", params=params)
        if rsp.status_code == 429:
            logging.info("Pausing for rate limit...")
            time.sleep(60)
            rsp = requests.get("https://explorer.lichess.ovh/master", params=params)

        return rsp.json()
    except:
        logging.warning("response: %s", rsp)


def get_moves_table(board):
    r = get_moves_table_fen(board.fen())

    total_moves = r["white"] + r["black"] + r["draws"]

    table = {}

    # arbitrary number chosen for when we consider it unreliable/not useful/
    # not popular enough to bother analyzing
    if total_moves < 200:
        return table

    for move in r["moves"]:
        count = move["white"] + move["black"] + move["draws"]
        table[chess.Move.from_uci(move["uci"])] = (count / total_moves, count)

    return table


def get_opposing_moves(board, min_moves=2, min_pct=0.05):
    table = get_moves_table(board)

    pass_pct = [
        k for k in table.keys() if table[k][0] > min_pct or table[k][1] > 1_000_000
    ]

    if len(pass_pct) > min_moves:
        return pass_pct

    return sorted(table.keys(), key=lambda k: -table[k][0])[:min_moves]


def prune(q):
    logging.info("Pruning %d...", len(q))

    totals = {}
    for b in q:
        tbl = get_moves_table(b)
        totals[b.fen()] = sum(c for _, c in tbl.values())

        if len(totals) % 20 == 0:
            logging.info("%d...", len(totals))

    sorted_q = sorted(q, key=lambda b: -totals[b.fen()])
    mid = len(sorted_q) // 2

    logging.info("Pruned to %d.", mid)

    return collections.deque(sorted_q[:mid]), sorted_q[mid:]


OPENING_MOVES = [chess.Move.from_uci(m) for m in ("e2e4", "d2d4", "c2c4", "g1f3")]


def build(heuristic, color):
    best_moves = {}

    q = collections.deque()
    terminal = []
    ply = 0

    if color == chess.WHITE:
        q.appendleft(chess.Board())
    else:
        for m in OPENING_MOVES:
            board = chess.Board()
            board.push(m)
            q.appendleft(board)

    while q:
        board = q.pop()
        fen = board.fen()

        if board.ply() != ply and board.ply() < 6:
            ply = board.ply()
        elif board.ply() != ply:
            q, t = prune(q)
            terminal.extend(t)
            ply = board.ply()

        best = best_moves.get(fen)

        if not best:
            best = find_best_move(board, heuristic)
            best_moves[fen] = best

        logging.info("q: %d, ply: %d, %s", len(q), board.ply(), board.san(best))

        board.push(best)

        if board.ply() < MAX_PLY + color and (opp_moves := get_opposing_moves(board)):
            for m in opp_moves:
                board_copy = board.copy()
                board_copy.push(m)
                q.appendleft(board_copy)
        else:
            terminal.append(board)

    depths = {}
    for b in terminal:
        depths[b.ply()] = depths.get(b.ply(), 0) + 1
        game = chess.pgn.Game()
        moves = b.move_stack

        node = game.add_main_variation(moves[0])

        for m in moves[1:]:
            node = node.add_main_variation(m)

        print(game, end="\n\n")

    logging.info("Depths: %s", depths)


try:
    what = sys.argv[1]

    if what == "licw":
        logging.info("Lichess winrate for white...")
        build(lichess_winrate, chess.WHITE)

    if what == "licb":
        logging.info("Lichess winrate for black...")
        build(lichess_winrate, chess.BLACK)

    if what == "masw":
        logging.info("Masters winrate for white...")
        build(masters_winrate, chess.WHITE)

    if what == "masb":
        logging.info("Masters winrate for black...")
        build(masters_winrate, chess.BLACK)

finally:
    engine.quit()
