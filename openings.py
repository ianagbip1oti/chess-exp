import sys
import functools
import requests
import queue
import chess
import chess.engine
import chess.pgn
import logging

logging.basicConfig(level=logging.INFO)

MAX_PLY = 4

engine = chess.engine.SimpleEngine.popen_uci("/usr/bin/stockfish")


def winning(board, pov):
    score = engine.analyse(board, chess.engine.Limit(depth=15))
    return score["score"].pov(pov).wdl(model="sf12", ply=board.ply()).winning_chance()


def dontlose(board, pov):
    score = engine.analyse(board, chess.engine.Limit(depth=15))
    wdl = score["score"].pov(pov).wdl(model="sf12", ply=board.ply())
    return 1.0 - wdl.losing_chance()


# We force modern by only allowing knight, biship and b/g pawn moves in first two
MODERN_FROM_SQUARES = {
    chess.B2,
    chess.G2,
    chess.C1,
    chess.F1,
    chess.B1,
    chess.A3,
    chess.C3,
    chess.G1,
    chess.F3,
    chess.H3,
    chess.B7,
    chess.G7,
    chess.C8,
    chess.F8,
    chess.B8,
    chess.A6,
    chess.C6,
    chess.G8,
    chess.F6,
    chess.H8,
}
def force_modern(board, pov):
    if board.ply() <= 4 and not board.peek().from_square in MODERN_FROM_SQUARES:
        return chess.engine.Mate(-0)

    return engine.analyse(board, chess.engine.Limit(depth=15))["score"].pov(pov)


def find_best_move(board, heuristic):
    moves = []

    for m in board.legal_moves:
        board_copy = board.copy()
        board_copy.push(m)
        moves.append((m, heuristic(board_copy, board.turn)))

    return sorted(moves, key=lambda x: -x[1])[0][0]


def get_moves_table(board):
    return get_moves_table_fen(board.fen())


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

    r = requests.get("https://explorer.lichess.ovh/lichess", params=params).json()

    total_moves = r["white"] + r["black"] + r["draws"]

    table = {}

    for move in r["moves"]:
        count = move["white"] + move["black"] + move["draws"]
        table[chess.Move.from_uci(move["uci"])] = count / total_moves

    return table


def get_opposing_moves(board, min_moves=2, min_pct=0.05):
    table = get_moves_table(board)

    pass_pct = [k for k in table.keys() if table[k] > min_pct]

    if len(pass_pct) > min_moves:
        return pass_pct

    return sorted(table.keys(), key=lambda k: -table[k])[:min_moves]


OPENING_MOVES = [chess.Move.from_uci(m) for m in ("e2e4", "d2d4", "c2c4", "g1f3")]


def build(heuristic, color):
    best_moves = {}

    q = []
    terminal = []

    if color == chess.WHITE:
        q.append(chess.Board())
    else:
        for m in OPENING_MOVES:
            board = chess.Board()
            board.push(m)
            q.append(board)

    while q:
        board = q.pop()
        fen = board.fen()

        best = best_moves.get(fen)

        if not best:
            best = find_best_move(board, heuristic)
            best_moves[fen] = best

        logging.info("q: %d, ply: %d, %s", len(q), board.ply(), board.san(best))

        board.push(best)

        if board.ply() < MAX_PLY - color and (opp_moves := get_opposing_moves(board)):
            for m in opp_moves:
                board_copy = board.copy()
                board_copy.push(m)
                q.append(board_copy)
        else:
            terminal.append(board)

    for b in terminal:
        game = chess.pgn.Game()
        moves = b.move_stack

        node = game.add_main_variation(moves[0])

        for m in moves[1:]:
            node = node.add_main_variation(m)

        print(game, end="\n\n")


try:
    what = sys.argv[1]

    if what == "winw":
        logging.info("Winning for white...")
        build(winning, chess.WHITE)

    if what == "winb":
        logging.info("Winning for black...")
        build(winning, chess.BLACK)

    if what == "losew":
        logging.info("Don't lose for white...")
        build(dontlose, chess.WHITE)

    if what == "loseb":
        logging.info("Don't lose for black...")
        build(dontlose, chess.BLACK)

    if what == "modw":
        logging.info("Modern for white...")
        build(force_modern, chess.WHITE)

    if what == "modb":
        logging.info("Modern for black...")
        build(force_modern, chess.BLACK)
finally:
    engine.quit()
