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
import math

logging.basicConfig(level=logging.INFO)

# 14 = 14/2 = 7 for black, 8 for white
MAX_PLY = 24

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
    r = get_moves_table(board.fen())

    total = r["white"] + r["black"] + r["draws"]
    wins = r["white"] if pov == chess.WHITE else r["black"]

    if total == 0:
        return 0.0

    z = 1.96
    phat = wins / total

    a = phat + z * z / (2 * total)
    b = z * math.sqrt((phat * (1 - phat) + z * z / (4 * total)) / total)
    c = 1 + z * z / total

    return (a - b) / c


def find_best_move(board, heuristic):
    before = heuristic(board, board.turn)

    moves = []

    table = get_moves_table(board)

    candidates = sorted(table.keys(), key=lambda k: -table[k][0])[:5]

    for m in candidates:
        board_copy = board.copy()
        board_copy.push(m)
        moves.append((m, heuristic(board_copy, board.turn)))

    top_score = sorted(moves, key=lambda x: -x[1])[0][1]

    if top_score < 0.98 * before:
        logging.info(
            "Falling back to stockfish (%f) for %s", top_score - before, board.fen()
        )
        for m in board.legal_moves:
            board_copy = board.copy()
            board_copy.push(m)

            sf = (
                winning(board_copy, board.turn)
                .wdl(model="lichess", ply=board_copy.ply())
                .winning_chance()
            )
            moves.append((m, sf))

    return sorted(moves, key=lambda x: -x[1])[0][0]


@functools.cache
def get_moves_table_fen(fen, speeds=None, ratings=None):
    speeds = speeds or ["blitz", "rapid", "classical"]
    ratings = ratings or [1600, 1800, 2000, 2200]

    params = {
        "fen": fen,
        "moves": 15,
        "topGames": 0,
        "recentGames": 0,
        "variant": "standard",
        "speeds[]": speeds,
        "ratings[]": ratings,
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
        k for k in table.keys() if table[k][0] > min_pct or table[k][1] > 100_000
    ]

    if len(pass_pct) > min_moves:
        return pass_pct

    return sorted(table.keys(), key=lambda k: -table[k][0])[:min_moves]


def prune(q, amt):
    logging.info("Pruning %d...", len(q))

    if len(q) < amt:
        logging.info("Pruned nothing to %d.", amt)
        return q, []

    deduped = []
    terminal = []

    totals = {}
    for b in q:
        if b.fen() in totals:
            terminal.append(b)
            continue

        tbl = get_moves_table(b)
        totals[b.fen()] = sum(c for _, c in tbl.values())

        deduped.append(b)

        if len(totals) % 20 == 0:
            logging.info("%d...", len(totals))

    sorted_q = sorted(deduped, key=lambda b: -totals[b.fen()])

    logging.info("Pruned to %d. (%d dupes)", amt, len(terminal))

    return collections.deque(sorted_q[:amt]), sorted_q[amt:] + terminal


def build(heuristic, color, max_ply=MAX_PLY):
    best_moves = {}

    q = collections.deque()
    terminal = []
    ply = 0

    if color == chess.WHITE:
        q.appendleft(chess.Board())
    else:
        board = chess.Board()
        for m in get_opposing_moves(board):
            board_copy = board.copy()
            board_copy.push(m)
            q.appendleft(board_copy)

    while q:
        board = q.pop()
        fen = board.board_fen()

        if board.ply() != ply:
            ply = board.ply()
            q, t = prune(q, ply * 25)
            terminal.extend(t)

        best = best_moves.get(fen)

        if not best:
            best = find_best_move(board, heuristic)
            best_moves[fen] = best

        logging.info("q: %d, ply: %d, %s", len(q), board.ply(), board.san(best))

        board.push(best)

        if board.ply() < max_ply + color and (opp_moves := get_opposing_moves(board)):
            for m in opp_moves:
                board_copy = board.copy()
                board_copy.push(m)
                q.appendleft(board_copy)
        else:
            terminal.append(board)

    depths = {}
    for b in terminal:
        if b.turn == color:
            b.pop()

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
    ply = int(sys.argv[2]) if len(sys.argv) > 2 else MAX_PLY

    if what == "licw":
        logging.info("Lichess winrate for white...")
        build(lichess_winrate, chess.WHITE, max_ply=ply)

    if what == "licb":
        logging.info("Lichess winrate for black...")
        build(lichess_winrate, chess.BLACK, max_ply=ply)

    if what == "masw":
        logging.info("Masters winrate for white...")
        build(masters_winrate, chess.WHITE, max_ply=ply)

    if what == "masb":
        logging.info("Masters winrate for black...")
        build(masters_winrate, chess.BLACK, max_ply=ply)
finally:
    engine.quit()
