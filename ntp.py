import socket
import time


NTP_EPOCH = 2208988800


def get_ntp_time(server="pool.ntp.org", timeout=3, retries=3):

    addresses = socket.getaddrinfo(server, 123)

    msg = bytearray(48)
    msg[0] = 0x1B

    last_error = None

    for result in addresses:

        addr = result[-1]

        for attempt in range(1, retries + 1):

            print("Trying NTP server:", addr,
                  "attempt", attempt, "/", retries)

            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(timeout)

            try:
                s.sendto(msg, addr)

                data, addr = s.recvfrom(48)

                if len(data) < 48:
                    raise OSError("Invalid NTP response")

                seconds = (
                    (data[40] << 24)
                    | (data[41] << 16)
                    | (data[42] << 8)
                    | data[43]
                )

                unix_time = seconds - NTP_EPOCH

                print("NTP response from:", addr)

                return time.gmtime(unix_time)

            except Exception as e:
                print("NTP failed:", addr, e)
                last_error = e

            finally:
                s.close()

    raise last_error