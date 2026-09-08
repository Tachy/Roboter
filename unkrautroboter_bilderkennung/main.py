"""
Hauptskript zum Starten des Unkrautroboters.
"""

from src.robot_control import get_robot

if __name__ == "__main__":
    get_robot().run()
