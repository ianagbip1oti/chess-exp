import chess
import chess.engine
import requests
import sys

engine = chess.engine.SimpleEngine.popen_uci("/usr/bin/stockfish")


def get_moves_table(board):
    params = {
        "fen": board.fen(),
        "moves": 15,
        "topGames": 0,
        "recentGames": 0,
        "variant": "standard",
        "speeds[]": ["blitz", "rapid", "classical"],
        "ratings[]": [1600,  1800, 2000, 2200],
    }

    r = requests.get("https://explorer.lichess.ovh/lichess", params=params).json()

    total_moves = r["white"] + r["black"] + r["draws"]

    table = {}

    for move in r["moves"]:
        count = move["white"] + move["black"] + move["draws"]
        table[move["uci"]] = count / total_moves

    return table


def analyse_move(board, move):
    board.push(move)
    score = engine.analyse(board, chess.engine.Limit(depth=15))
    board.pop()
    return score["score"]


# the value of the 2nd best move against
# So, if this board currenty says whites move, we're evaluating
# how good this position is for black
def score(board):
    table = get_moves_table(board)

    scores = (
        (m.uci(), analyse_move(board, m).pov(not board.turn)) for m in board.legal_moves
    )
    scores = sorted(scores, key=lambda x: x[1])

    # we default to 1, rather than zero, as the 1st move could
    # be such an obvious blunder that no one has ever played it
    correct_probability = table.get(scores[0][0], 1)

    print(scores[:2], correct_probability)

    if scores[0][1].is_mate():
        return 0.0

    exp_1 = scores[0][1].wdl(model="sf12", ply=board.ply()).winning_chance()
    exp_2 = scores[0][1].wdl(model="sf12", ply=board.ply()).winning_chance()

    return correct_probability * exp_1 + (1 - correct_probability) * exp_2

    # return (
    #    0.0 if scores[0][1].is_mate()
    #    else scores[1][1].wdl(model="sf12", ply=board.ply()).expectation()
    # )
    # return scores[0][1] if scores[0][1].is_mate() else scores[1][1]


# The strategy here is to give our opponent only one good line
# meaning we're looking to play moves where any response except the best is bad
# So we evaluate based upon the 2nd best move against us
def find_best_move(board):
    moves = []

    for m in board.legal_moves:
        board.push(m)
        print(f"vs {m} ", end="")
        moves.append((m.uci(), score(board)))
        sys.stdout.flush()
        board.pop()

    moves = sorted(moves, key=lambda x: -x[1])
    print("allow_one:" + str(moves[:3]))

# This strategy uses sf12s win/draw/loss model and chooses the move with the
# best chance of a win (don't play for a draw)
def find_winningest_move(board):
    moves = (
        (
            m.uci(),
            analyse_move(board, m)
            .pov(board.turn)
            .wdl(model="sf12", ply=board.ply())
            .winning_chance(),
        )
        for m in board.legal_moves
    )

    moves = sorted(moves, key=lambda x: -x[1])
    print("winningest:" + str(moves[:3]))
    return moves

# This strategy uses sf12s win/draw/loss model and chooses the move with the
# best chance of a win or a draw
def find_dontlose_move(board):
    moves = (
        (
            m.uci(),
            analyse_move(board, m)
            .pov(board.turn)
            .wdl(model="sf12", ply=board.ply())
        )
        for m in board.legal_moves
    )

    moves = ((m, s.winning_chance() + s.drawing_chance()) for m, s in moves)

    moves = sorted(moves, key=lambda x: -x[1])
    print("dontlose:" + str(moves[:3]))
    return moves


fen = " ".join(sys.argv[1:])
board = chess.Board(fen)

print(fen)
try:
    print(board)
    print(board.ply(), board.fullmove_number)
    find_winningest_move(board)
    find_dontlose_move(board)
    find_best_move(board)
finally:
    engine.quit()
