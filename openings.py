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
session = requests.Session()


def winning(board, pov, depth=15):
    score = engine.analyse(board, chess.engine.Limit(depth=depth))
    return score["score"].pov(pov)


def prune_candidates(candidates, board, pov, depth, prune):
    scores = []
    for c in candidates:
        board_copy = board.copy()
        board_copy.push(c)
        score = winning(board_copy, pov=pov, depth=depth)
        scores.append((c, score))

    scores = sorted(scores, key=lambda s: -s[1])
    return [s[0] for s in scores][:prune]


def easy_stockfish(board, pov, *args, **kwargs):
    candidates = board.legal_moves

    candidates = prune_candidates(candidates, board, pov, 3, 5)
    candidates = prune_candidates(candidates, board, pov, 6, 4)
    candidates = prune_candidates(candidates, board, pov, 9, 3)
    candidates = prune_candidates(candidates, board, pov, 12, 2)
    candidates = prune_candidates(candidates, board, pov, 15, 1)

    return candidates[0]


def stockfish(board, pov):
    scores = [
        engine.analyse(board, chess.engine.Limit(depth=d))["score"].pov(pov).score(mate_score=10000)
        for d in (5, 10, 15)
    ]

    return sum(a * b for a, b in zip(scores, (3, 2, 1)))



# Choose most played moves (same logic as opposition moves we consider)
# Choose the one with the highest winning pct
def lichess_winrate(board, pov):
    return winrate(board, pov, get_moves_table_fen)


def masters_winrate(board, pov):
    return winrate(board, pov, get_masters_table_fen)


def winrate(board, pov, get_moves_table):
    if board.is_checkmate():
        return 1.0 if board.turn != pov else 0.0

    r = get_moves_table(board.fen())

    total = r["white"] + r["black"] + r["draws"]
    wins = r["white"] if pov == chess.WHITE else r["black"]

    if total == 0:
        return 0.0

    return wsi_lower(wins, total)


def wsi_lower(wins, total):
    z = 1.96
    phat = wins / total
    n = total

    a = phat + z * z / (2 * total)
    b = z * math.sqrt((phat * (1 - phat) + z * z / (4 * total)) / total)
    c = 1 + z * z / total

    return (a - b) / c


def find_best_move(board, heuristic, *args, **kwargs):
    min_pct = 0.05
    before = heuristic(board, board.turn)

    moves = []

    table = get_moves_table(board, min_moves=0)

    pass_pct = [
        k for k in table.keys() if table[k][0] > min_pct or table[k][1] > 100_000
    ]

    if len(pass_pct) >= 3:
        candidates = sorted(pass_pct, key=lambda k: -table[k][0])[:5]
    else:
        candidates = sorted(table.keys(), key=lambda k: -table[k][0])[:3]

    for m in candidates:
        board_copy = board.copy()
        board_copy.push(m)
        moves.append((m, heuristic(board_copy, board.turn)))

    top_score = 0.0

    if moves:
        top_score = sorted(moves, key=lambda x: -x[1])[0][1]

    not_enough_candidates = len(moves) < 2
    unusual_drop = top_score < 0.95 * before
    unusually_low = top_score - before > 0.01 and top_score < 0.45

    if any((not_enough_candidates, unusual_drop, unusually_low)):
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
    ratings = ratings or [1600]

    params = {
        "fen": fen,
        "moves": 10,
        "topGames": 0,
        "recentGames": 0,
        "variant": "standard",
        "speeds[]": speeds,
        "ratings[]": ratings,
    }

    retry_count = 0
    while retry_count < 3:
        rsp = None  # default to something for exception logging
        try:
            rsp = session.get("https://explorer.lichess.ovh/lichess", params=params)
            if rsp.status_code == 429:
                logging.info("Pausing for rate limit...")
                time.sleep(60)
            else:
                return rsp.json()
        except:
            logging.warning("response: %s", rsp)

            logging.info("Pausing before retry...")
            time.sleep(300)

        retry_count += 1


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
        rsp = session.get("https://explorer.lichess.ovh/master", params=params)
        if rsp.status_code == 429:
            logging.info("Pausing for rate limit...")
            time.sleep(60)
            rsp = session.get("https://explorer.lichess.ovh/master", params=params)

        return rsp.json()
    except:
        logging.warning("response: %s", rsp)


def get_moves_table(board, min_moves=200):
    r = get_moves_table_fen(board.fen())

    total_moves = r["white"] + r["black"] + r["draws"]

    table = {}

    if total_moves < min_moves:
        return table

    for move in r["moves"]:
        count = move["white"] + move["black"] + move["draws"]
        table[chess.Move.from_uci(move["uci"])] = (count / total_moves, count)

    return table


def get_opposing_moves(board, min_moves=2, min_pct=0.5):
    # arbitrary number chosen for when we consider it unreliable/not useful/
    # not popular enough to bother analyzing
    table = get_moves_table(board, min_moves=200)

    pass_pct = [
        k for k in table.keys() if table[k][0] > min_pct or table[k][1] > 100_000
    ]

    if len(pass_pct) >= min_moves:
        return pass_pct[:10]

    return sorted(table.keys(), key=lambda k: -table[k][0])[:min_moves]


def prune(q, amt):
    logging.info("Pruning %d...", len(q))

    deduped = []
    terminal = []

    totals = {}
    for b in q:
        if b.fen() in totals:
            terminal.append(b)
            continue

        tbl = get_moves_table(b)
        total = sum(c for _, c in tbl.values())
        totals[b.fen()] = total

        if total < 200:
            terminal.append(b)
            continue
        else:
            deduped.append(b)

        if len(totals) % 20 == 0:
            logging.info("%d...", len(totals))

    sorted_q = sorted(deduped, key=lambda b: -totals[b.fen()])

    logging.info("Pruned to %d. (%d dupes/uninteresting)", amt, len(terminal))

    return collections.deque(sorted_q[:amt]), sorted_q[amt:] + terminal


def build(heuristic, color, max_ply=MAX_PLY, prune_factor=20, find_best_move=find_best_move):
    best_moves = {}

    q = collections.deque()
    terminal = []
    ply = 0

    if color == chess.WHITE:
        board = chess.Board()
        best = find_best_move(board, heuristic, color)
        logging.info("q: %d, ply: %d, %s", len(q), board.ply(), board.san(best))
        board.push(best)
        q.appendleft(board)
    else:
        q.appendleft(chess.Board())

    while q:
        if (next_ply := q[-1].ply()) != ply:
            ply = next_ply
            q, t = prune(q, ply * prune_factor)
            terminal.extend(t)

        if not q:
            break

        board = q.pop()

        logging.info("q: %d, ply: %d", len(q), board.ply())

        if board.ply() < max_ply + color and (opp_moves := get_opposing_moves(board)):
            for m in opp_moves:
                board_copy = board.copy()
                board_copy.push(m)

                fen = board_copy.board_fen()
                best = best_moves.get(fen)

                if not best:
                    best = find_best_move(board_copy, heuristic, color)
                    best_moves[fen] = best

                logging.info("vs %s... %s", board.san(m), board_copy.san(best))

                board_copy.push(best)

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


WHATS = {
    "licw": dict(heuristic=lichess_winrate, color=chess.WHITE),
    "licb": dict(heuristic=lichess_winrate, color=chess.BLACK),
    "licw2": dict(heuristic=lichess_winrate, color=chess.WHITE, prune_factor=2),
    "licb2": dict(heuristic=lichess_winrate, color=chess.BLACK, prune_factor=2),
    "licw5": dict(heuristic=lichess_winrate, color=chess.WHITE, prune_factor=5),
    "licb5": dict(heuristic=lichess_winrate, color=chess.BLACK, prune_factor=5),
    "masw": dict(heuristic=masters_winrate, color=chess.WHITE),
    "masb": dict(heuristic=masters_winrate, color=chess.BLACK),
    "stkw": dict(heuristic=None, find_best_move=easy_stockfish, color=chess.WHITE, prune_factor=3, max_ply=30),
    "stkb": dict(heuristic=None, find_best_move=easy_stockfish, color=chess.BLACK, prune_factor=3, max_ply=30),
}
try:
    what = sys.argv[1]
    ply = int(sys.argv[2]) if len(sys.argv) > 2 else MAX_PLY

    args = WHATS[what]

    if "max_ply" not in args:
        max_ply = ply

    logging.info("Building for %s", args)
    build(**args)
finally:
    engine.quit()
