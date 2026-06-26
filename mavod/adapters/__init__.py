"""Adapters : tous les I/O isolés derrière des interfaces typées.

Chaque adapter consomme une `Settings` (mavod.config) et renvoie des
dataclasses du `domain/` plutôt que des dict bruts. Permet de mocker
proprement les services et de séparer logique métier (services/) de
l'intégration externe.
"""
