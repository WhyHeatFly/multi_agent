import time


def main() -> None:
    print("cultural-ip worker started")
    while True:
        time.sleep(30)
        print("cultural-ip worker heartbeat")


if __name__ == "__main__":
    main()

