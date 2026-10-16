import urequests


TIMEZONE_URL = "https://time.now/developer/api/ip"


def get_timezone():

    response = None

    try:
        response = urequests.get(
            TIMEZONE_URL,
            headers={
                "User-Agent": "Pico-W-MicroPython",
                "Accept": "application/json",
            }
        )

        if response.status_code != 200:
            raise OSError(
                "Timezone HTTP error: {}".format(
                    response.status_code
                )
            )

        data = response.json()

        return {
            "timezone": data["timezone"],
            "utc_offset": data["utc_offset"],
            "dst": data["dst"],
        }

    finally:
        if response is not None:
            response.close()