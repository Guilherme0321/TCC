"""Módulo de exemplo mínimo para os testes de integração do executor."""


def soma(a, b):
    return a + b


def divide(a, b):
    if b == 0:
        raise ValueError("divisão por zero")
    return a / b


def classifica(n):
    if n < 0:
        return "negativo"
    if n == 0:
        return "zero"
    return "positivo"
