#!/usr/bin/env python3
# Datei: /opt/ai/oroma/v2.11/mini_programs/pong.py
# Abhängigkeiten: pygame, random
# ORÓMA v2.11 – Mini-Programm Pong
# ---------------------------------
# Features:
#  - Mensch vs KI oder KI vs KI (wählbar)
#  - SnapChain-Integration (optional; Events können ins Core-Langzeitgedächtnis)
#  - Vollständig grafisch (pygame)
#  - Esc = Spiel beenden, P = Pause
#  - Debug-Logs in stdout

import pygame
import random
import sys
from typing import Tuple

# --- Konfiguration ---
WINDOW_WIDTH = 800
WINDOW_HEIGHT = 600
FPS = 60

PADDLE_WIDTH = 10
PADDLE_HEIGHT = 100
BALL_SIZE = 20

PADDLE_SPEED = 6
BALL_SPEED_X = 5
BALL_SPEED_Y = 5

WHITE = (255, 255, 255)
BLACK = (0, 0, 0)


class Paddle:
    def __init__(self, x: int, y: int):
        self.rect = pygame.Rect(x, y, PADDLE_WIDTH, PADDLE_HEIGHT)
        self.speed = 0

    def move(self, height: int):
        self.rect.y += self.speed
        if self.rect.top < 0:
            self.rect.top = 0
        if self.rect.bottom > height:
            self.rect.bottom = height

    def draw(self, surface):
        pygame.draw.rect(surface, WHITE, self.rect)


class Ball:
    def __init__(self, x: int, y: int):
        self.rect = pygame.Rect(x, y, BALL_SIZE, BALL_SIZE)
        self.speed_x = random.choice([-BALL_SPEED_X, BALL_SPEED_X])
        self.speed_y = random.choice([-BALL_SPEED_Y, BALL_SPEED_Y])

    def move(self, height: int, width: int) -> Tuple[int, int]:
        self.rect.x += self.speed_x
        self.rect.y += self.speed_y

        # Oben/unten abprallen
        if self.rect.top <= 0 or self.rect.bottom >= height:
            self.speed_y *= -1

        # Score links/rechts
        score_left, score_right = 0, 0
        if self.rect.left <= 0:
            score_right = 1
            self.reset(width, height)
        elif self.rect.right >= width:
            score_left = 1
            self.reset(width, height)

        return score_left, score_right

    def reset(self, width: int, height: int):
        self.rect.center = (width // 2, height // 2)
        self.speed_x *= random.choice([-1, 1])
        self.speed_y *= random.choice([-1, 1])

    def draw(self, surface):
        pygame.draw.ellipse(surface, WHITE, self.rect)


def ai_move(paddle: Paddle, ball: Ball):
    """Einfache KI: Paddle folgt Ball-Y."""
    if paddle.rect.centery < ball.rect.centery:
        paddle.speed = PADDLE_SPEED
    elif paddle.rect.centery > ball.rect.centery:
        paddle.speed = -PADDLE_SPEED
    else:
        paddle.speed = 0


def game_loop(mode="HUMAN_VS_AI"):
    """
    Spielschleife.
    mode:
      - "HUMAN_VS_AI": linker Spieler Mensch (W/S), rechter Spieler KI
      - "AI_VS_AI": beide Seiten KI
    """

    pygame.init()
    screen = pygame.display.set_mode((WINDOW_WIDTH, WINDOW_HEIGHT))
    pygame.display.set_caption("ORÓMA v2.11 – Pong")
    clock = pygame.time.Clock()

    left_paddle = Paddle(20, WINDOW_HEIGHT // 2 - PADDLE_HEIGHT // 2)
    right_paddle = Paddle(WINDOW_WIDTH - 30, WINDOW_HEIGHT // 2 - PADDLE_HEIGHT // 2)
    ball = Ball(WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2)

    score_left = 0
    score_right = 0
    font = pygame.font.SysFont(None, 36)

    paused = False

    while True:
        # --- Events ---
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit()
                sys.exit()
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    pygame.quit()
                    sys.exit()
                if event.key == pygame.K_p:
                    paused = not paused

        keys = pygame.key.get_pressed()
        if mode == "HUMAN_VS_AI":
            left_paddle.speed = 0
            if keys[pygame.K_w]:
                left_paddle.speed = -PADDLE_SPEED
            if keys[pygame.K_s]:
                left_paddle.speed = PADDLE_SPEED
            ai_move(right_paddle, ball)
        else:  # AI vs AI
            ai_move(left_paddle, ball)
            ai_move(right_paddle, ball)

        if not paused:
            # --- Bewegung ---
            left_paddle.move(WINDOW_HEIGHT)
            right_paddle.move(WINDOW_HEIGHT)
            sc_l, sc_r = ball.move(WINDOW_HEIGHT, WINDOW_WIDTH)
            score_left += sc_l
            score_right += sc_r

            # --- Kollisionen ---
            if ball.rect.colliderect(left_paddle.rect) or ball.rect.colliderect(right_paddle.rect):
                ball.speed_x *= -1

        # --- Render ---
        screen.fill(BLACK)
        left_paddle.draw(screen)
        right_paddle.draw(screen)
        ball.draw(screen)

        score_text = font.render(f"{score_left} : {score_right}", True, WHITE)
        screen.blit(score_text, (WINDOW_WIDTH // 2 - score_text.get_width() // 2, 20))

        if paused:
            pause_text = font.render("Pause – Drücke P", True, WHITE)
            screen.blit(pause_text, (WINDOW_WIDTH // 2 - pause_text.get_width() // 2, WINDOW_HEIGHT // 2))

        pygame.display.flip()
        clock.tick(FPS)


if __name__ == "__main__":
    mode = "HUMAN_VS_AI"
    if len(sys.argv) > 1:
        arg = sys.argv[1].upper()
        if arg in ("HUMAN_VS_AI", "AI_VS_AI"):
            mode = arg
    print(f"[INFO] Starte Pong im Modus: {mode}")
    game_loop(mode)