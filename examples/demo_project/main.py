from utils.helpers import greet
from utils.math_utils import add
from config import APP_NAME


def main():
    name = APP_NAME
    print(greet(name))
    print("1 + 2 =", add(1, 2))


if __name__ == "__main__":
    main()
