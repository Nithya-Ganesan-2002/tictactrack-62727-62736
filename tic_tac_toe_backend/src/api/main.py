import os
from fastapi import FastAPI, HTTPException, Depends, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from passlib.context import CryptContext
from jose import JWTError, jwt
from datetime import datetime, timedelta
import uuid

# --- CONFIGURATION ---

# SECRET_KEY, ALGORITHM, etc. would be securely loaded from ENV in production!
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 120


# --- APP INSTANTIATION ---

app = FastAPI(
    title="Tic Tac Toe Backend API",
    description="REST and WebSocket backend for classic Tic Tac Toe with persistent user/game state and leaderboard.",
    version="1.0.0",
    openapi_tags=[
        {"name": "User", "description": "User registration, login, stats"},
        {"name": "Game", "description": "Tic Tac Toe game management and play"},
        {"name": "Leaderboard", "description": "Scoreboard and rankings"},
        {"name": "WebSocket", "description": "Real-time game updates"},
    ],
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- AUTHENTICATION HELPER CLASSES ---

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

# --- IN-MEMORY MOCK DATABASE STRUCTURE ---

# In production, replace these with real DB connections via SQLAlchemy/ORM/driver
db_users: Dict[str, Dict[str, Any]] = {}
db_games: Dict[str, Dict[str, Any]] = {}
db_leaderboard: Dict[str, Any] = {}


# --- PERSISTENCE HELPERS (STUBS) ---


# PUBLIC_INTERFACE
def get_user(username: str) -> Optional[Dict[str, Any]]:
    """Get user by username."""
    return db_users.get(username)


# PUBLIC_INTERFACE
def create_user(username: str, password: str) -> Dict[str, Any]:
    """Create user and return user dict."""
    if username in db_users:
        raise HTTPException(status_code=400, detail="Username already registered")
    hashed_pwd = pwd_context.hash(password)
    user = {
        "username": username,
        "hashed_password": hashed_pwd,
        "created_at": datetime.utcnow(),
        "wins": 0,
        "losses": 0,
    }
    db_users[username] = user
    return user


# PUBLIC_INTERFACE
def verify_password(plain_password, hashed_password):
    """Verify password using hashing."""
    return pwd_context.verify(plain_password, hashed_password)


# PUBLIC_INTERFACE
def authenticate_user(username: str, password: str) -> Optional[Dict[str, Any]]:
    """Authenticate user and return user if credentials are valid."""
    user = get_user(username)
    if not user or not verify_password(password, user["hashed_password"]):
        return None
    return user


# PUBLIC_INTERFACE
def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create JWT access token."""
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=15))
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(
        to_encode,
        SECRET_KEY,
        algorithm=ALGORITHM
    )
    return encoded_jwt


# PUBLIC_INTERFACE
def get_current_user(token: str = Depends(oauth2_scheme)):
    """Dependency for getting current user from token."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    user = get_user(username)
    if user is None:
        raise credentials_exception
    return user


# --- MODELS & SCHEMAS ---

class Token(BaseModel):
    access_token: str
    token_type: str

class UserBase(BaseModel):
    username: str = Field(..., description="User's username")

class UserCreate(UserBase):
    password: str = Field(..., description="User's password")

class UserStats(UserBase):
    wins: int
    losses: int
    created_at: datetime

class Move(BaseModel):
    row: int = Field(..., ge=0, le=2, description="Row index (0-2)")
    col: int = Field(..., ge=0, le=2, description="Column index (0-2)")

class GameState(BaseModel):
    id: str = Field(..., description="Game ID (UUID)")
    player_x: str
    player_o: str
    board: List[List[Optional[str]]]
    turn: str
    winner: Optional[str] = None
    is_draw: bool = False
    created_at: datetime
    moves: int

class LeaderboardEntry(BaseModel):
    username: str
    wins: int
    losses: int


# --- CORE GAME LOGIC ---


# PUBLIC_INTERFACE
def new_game(player_x: str, player_o: Optional[str] = None) -> GameState:
    """Start a new tic-tac-toe game and return the game state."""
    game_id = str(uuid.uuid4())
    board = [[None, None, None] for _ in range(3)]
    game = {
        "id": game_id,
        "player_x": player_x,
        "player_o": player_o,
        "board": board,
        "turn": "X",
        "winner": None,
        "is_draw": False,
        "created_at": datetime.utcnow(),
        "moves": 0,
    }
    db_games[game_id] = game
    return GameState(**game)


# PUBLIC_INTERFACE
def join_game(username: str) -> GameState:
    """Player joins an open game or starts a new one if none open."""
    # Look for unfilled games
    for game in db_games.values():
        if game["player_o"] is None and game["player_x"] != username:
            game["player_o"] = username
            return GameState(**game)
    # Else, start a new one
    return new_game(player_x=username)


# PUBLIC_INTERFACE
def get_game(game_id: str) -> Optional[GameState]:
    """Get game state by ID."""
    if game_id not in db_games:
        return None
    return GameState(**db_games[game_id])


# PUBLIC_INTERFACE
def apply_move(game_id: str, username: str, move: Move) -> GameState:
    """Try to apply a move, update the game, and return updated GameState."""
    game = db_games.get(game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found.")

    # Determine symbol
    if username == game['player_x']:
        symbol = 'X'
    elif username == game['player_o']:
        symbol = 'O'
    else:
        symbol = None

    if symbol is None:
        raise HTTPException(status_code=403, detail="You are not a player in this game.")
    if game["winner"] or game["is_draw"]:
        raise HTTPException(status_code=400, detail="Game is over.")
    if symbol != game["turn"]:
        raise HTTPException(status_code=400, detail="Not your turn.")

    r, c = move.row, move.col
    if not (0 <= r < 3 and 0 <= c < 3):
        raise HTTPException(status_code=400, detail="Move coordinates out of range.")
    if game["board"][r][c] is not None:
        raise HTTPException(status_code=400, detail="Cell already occupied.")

    game["board"][r][c] = symbol
    game["moves"] += 1
    # Check win
    winner = calculate_winner(game["board"])
    if winner:
        game['winner'] = symbol
        update_wins_losses(game, symbol)
    elif game["moves"] == 9:
        game["is_draw"] = True
        update_wins_losses(game, None)
    else:
        game["turn"] = "O" if game["turn"] == "X" else "X"
    return GameState(**game)


def calculate_winner(board: List[List[Optional[str]]]) -> Optional[str]:
    """Return 'X', 'O' if there's a winner, else None."""
    lines = board + [list(x) for x in zip(*board)]
    lines += [
        [board[i][i] for i in range(3)],
        [board[i][2 - i] for i in range(3)],
    ]
    for line in lines:
        if line[0] and line[0] == line[1] == line[2]:
            return line[0]
    return None


def update_wins_losses(game: Dict[str, Any], symbol: Optional[str]):
    """Update wins/losses for both players upon game end."""
    # Only update stats if both players set
    x_user = db_users.get(game["player_x"])
    o_user = db_users.get(game["player_o"])
    if symbol == "X":
        if x_user:
            x_user["wins"] += 1
        if o_user:
            o_user["losses"] += 1
    elif symbol == "O":
        if o_user:
            o_user["wins"] += 1
        if x_user:
            x_user["losses"] += 1
    elif symbol is None:
        # Draw
        if x_user:
            x_user["losses"] += 1
        if o_user:
            o_user["losses"] += 1


# --- ENDPOINTS ---


@app.get("/", summary="Health check", tags=["User"])
def health_check():
    """Health check endpoint for the Tic Tac Toe backend."""
    return {"message": "Healthy"}

# --- Authentication & User Management ---

@app.post("/register", summary="Register new user", tags=["User"], response_model=UserStats)
def register(user: UserCreate):
    """Register a new user account."""
    created = create_user(user.username, user.password)
    return UserStats(
        username=created["username"],
        wins=created["wins"],
        losses=created["losses"],
        created_at=created["created_at"],
    )


@app.post("/token", summary="Get JWT access token", tags=["User"], response_model=Token)
def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends()):
    """User login. Returns JWT token if successful."""
    user = authenticate_user(form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token = create_access_token(
        data={"sub": user["username"]},
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    return {"access_token": access_token, "token_type": "bearer"}


@app.get("/user/me", summary="Get current user info", tags=["User"], response_model=UserStats)
def read_users_me(current_user: dict = Depends(get_current_user)):
    """Return stats about the currently authenticated user."""
    return UserStats(
        username=current_user["username"],
        wins=current_user["wins"],
        losses=current_user["losses"],
        created_at=current_user["created_at"],
    )


# --- Game Endpoints ---

@app.post("/game/join", summary="Join or start a new game", tags=["Game"], response_model=GameState)
def join_or_create_game(current_user: dict = Depends(get_current_user)):
    """Join an open game, or start a new one if none available."""
    return join_game(username=current_user["username"])


@app.get("/game/{game_id}", summary="Get game state", tags=["Game"], response_model=GameState)
def get_game_state(game_id: str, current_user: dict = Depends(get_current_user)):
    """Fetch the current state of a game by ID."""
    game = get_game(game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found.")
    if current_user["username"] not in [game.player_x, game.player_o]:
        raise HTTPException(status_code=403, detail="You are not a player in this game.")
    return game


@app.post("/game/{game_id}/move", summary="Make a move", tags=["Game"], response_model=GameState)
def make_move(game_id: str, move: Move, current_user: dict = Depends(get_current_user)):
    """Make a move in an ongoing game."""
    return apply_move(game_id, current_user["username"], move)


# --- Leaderboard ---


@app.get("/leaderboard", summary="Leaderboard", tags=["Leaderboard"], response_model=List[LeaderboardEntry])
def get_leaderboard():
    """Get leaderboard stats for all users."""
    all_stats = [
        LeaderboardEntry(
            username=u["username"],
            wins=u["wins"],
            losses=u["losses"]
        )
        for u in db_users.values()
    ]
    sorted_stats = sorted(all_stats, key=lambda x: (-x.wins, x.losses, x.username))
    return sorted_stats[:10]


# --- WebSocket for Live Game Updates ---


class ConnectionManager:
    """Manages WS connections for live game updates."""

    def __init__(self):
        self.active_connections: Dict[str, List[WebSocket]] = {}

    # PUBLIC_INTERFACE
    async def connect(self, game_id: str, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.setdefault(game_id, []).append(websocket)

    # PUBLIC_INTERFACE
    def disconnect(self, game_id: str, websocket: WebSocket):
        self.active_connections[game_id].remove(websocket)
        if not self.active_connections[game_id]:
            del self.active_connections[game_id]

    # PUBLIC_INTERFACE
    async def broadcast(self, game_id: str, message: dict):
        if game_id not in self.active_connections:
            return
        for ws in self.active_connections[game_id]:
            await ws.send_json(message)


manager = ConnectionManager()


@app.websocket("/ws/game/{game_id}")
async def websocket_endpoint(websocket: WebSocket, game_id: str, token: str = ""):
    """
    WebSocket endpoint for real-time game updates.
    Pass a valid JWT token as a query param (?token=).
    """
    # Authenticate JWT
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if not username:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
    except JWTError:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    await manager.connect(game_id, websocket)
    try:
        while True:
            data = await websocket.receive_json()
            # For demonstration, immediately broadcast the data to all peers in the game.
            await manager.broadcast(game_id, data)
    except WebSocketDisconnect:
        manager.disconnect(game_id, websocket)


@app.get(
    "/ws-help",
    tags=["WebSocket"],
    summary="WebSocket Docs",
    response_class=JSONResponse,
)
def ws_help():
    """
    Usage:
    - Connect to wss://<host>/ws/game/{game_id}?token=<JWT>
    - Receives: broadcasts of board changes for game_id.
    - Send: {"move": {"row": 1, "col": 1}} to broadcast moves (for front-end demoing).
    """
    return {
        "info": [
            "Connect to /ws/game/{game_id}?token=<JWT>",
            "Use the REST endpoint to make moves; WebSocket is for real-time board updates.",
            "WebSocket receives JSON broadcasts for board/game state."
        ]
    }
