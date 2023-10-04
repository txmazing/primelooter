"""Script to claim Prime Gaming Loot automatically."""
import argparse
import json
import logging
import os
import sys
import time
import traceback
import typing
from http import cookiejar

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Cookie,
    Error,
    Page,
    sync_playwright,
)

handler = logging.StreamHandler(sys.stdout)

logging.basicConfig(
    level=logging.INFO,
    format="{asctime} [{levelname}] {message}",
    style="{",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.FileHandler("primelooter.log"), handler],
)

log = logging.getLogger()


class AuthException(Exception):
    """Exception raised for errors in the authentication process."""


class ClaimException(Exception):
    """Exception raised for errors in the claiming process."""


class PrimeLooter:
    """Class for looting Prime Gaming Loot"""

    def __init__(
        self, cookies, publishers="all", headless=True, debug=False, use_chrome=True
    ):
        self.cookies = cookies
        self.publishers = publishers
        self.headless = headless
        self.debug = debug
        self.use_chrome = use_chrome
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None

    def __enter__(self):
        """Start the browser and create a new page."""

        self.playwright = sync_playwright()

        if self.use_chrome:
            self.browser: Browser = self.playwright.start().chromium.launch(
                headless=self.headless
            )
        else:
            self.browser: Browser = self.playwright.start().firefox.launch(
                headless=self.headless
            )

        if self.debug and not os.path.exists("./dumps"):
            os.makedirs("./dumps")

        self.context: BrowserContext = self.browser.new_context()
        self.context.add_cookies(self.cookies)
        self.page: Page = self.context.new_page()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.page.close()
        self.context.close()
        self.browser.close()
        self.playwright.__exit__()

    @staticmethod
    def code_to_file(
        game: str, code: str, instructions: str, seperator_string: str = ""
    ) -> None:
        """Write the code and instructions to a file."""

        seperator_string = (
            seperator_string or "========================\n========================"
        )
        with open("./game_codes.txt", "a", encoding="utf-8") as code_file:
            code_file.write(
                f"{game}: {code}\n\n{instructions.replace('/n',' ')}\n{seperator_string}\n"
            )
        log.info("Code for %s written to file!", game)

    @staticmethod
    def exists(tab: Page, selector: str) -> bool:
        """Check if an element exists on the page."""

        if tab.query_selector(selector):
            return True
        return False

    def auth(self) -> None:
        """Authenticate the user."""

        with self.page.expect_response(
            lambda response: "https://gaming.amazon.com/graphql" in response.url
            and "currentUser" in response.json()["data"]
        ) as response_info:
            log.debug("get auth info")
            self.page.goto("https://gaming.amazon.com/home")
            response = response_info.value.json()["data"]["currentUser"]
            if not response["isSignedIn"]:
                raise AuthException(
                    "Authentication: Not signed in. (Please recreate the cookie.txt file)"
                )

            if not response["isAmazonPrime"]:
                raise AuthException(
                    "Authentication: Not a valid Amazon Prime account. "
                    "(Loot can only be redeemed with an Amazon Prime Membership)"
                )

            if not response["isTwitchPrime"]:
                raise AuthException(
                    "Authentication: Not a valid Twitch Prime account. "
                    "(Loot can only be redeemed with an Amazon Prime "
                    "subscription and a connected Twitch Prime account)"
                )

    def get_games_offers(self) -> typing.List:
        """Get all games offers."""

        with self.page.expect_response(
            lambda response: "https://gaming.amazon.com/graphql" in response.url
            and "games" in response.json()["data"]
        ) as response_info:
            log.debug("get games offers")
            self.page.goto("https://gaming.amazon.com/home")
            return response_info.value.json()["data"]["games"]["items"]

    def get_ingameloot_offers(self) -> typing.List:
        """Get all ingameloot offers."""

        with self.page.expect_response(
            lambda response: "https://gaming.amazon.com/graphql" in response.url
            and "inGameLoot" in response.json()["data"]
        ) as response_info:
            log.debug("get inGameLoot offers")
            self.page.goto("https://gaming.amazon.com/home")
            return response_info.value.json()["data"]["inGameLoot"]["items"]

    @staticmethod
    def check_is_claimed(offer: dict) -> bool:
        """Check if an offer has been claimed already."""

        if offer["offers"]:
            for suboffer in offer["offers"]:
                if suboffer["offerSelfConnection"]["eligibility"]:
                    return suboffer["offerSelfConnection"]["eligibility"]["isClaimed"]

        raise ClaimException(
            f"Could not check_is_claimed status!\n{json.dumps(offer, indent=4)}"
        )

    def claim(self, url) -> str:
        """Claim an offer."""

        tab = self.context.new_page()
        try:
            with tab.expect_response(
                lambda response: "https://gaming.amazon.com/graphql" in response.url
                and "item" in response.json()["data"]
            ) as response_info:
                log.debug("get offer title info")
                tab.goto(url)

                item = response_info.value.json()["data"]["item"]

            is_fgwp = item["isFGWP"]
            game_name = item["game"]["assets"]["title"]
            publisher = item["game"]["assets"]["publisher"]
            grants_code = item["grantsCode"]

            if publisher and "all" not in self.publishers:
                log.debug(
                    "Publisher is not in the list of publishers! (%s from %s)",
                    game_name,
                    publisher,
                )
                return

            claimable_offers = [
                offer
                for offer in item["offers"]
                if offer["offerSelfConnection"]["eligibility"]["canClaim"]
            ]
            not_claimable_offers = [
                offer
                for offer in item["offers"]
                if not offer["offerSelfConnection"]["eligibility"]["canClaim"]
            ]

            if not_claimable_offers:
                for not_claimable_offer in not_claimable_offers:
                    offer_state = not_claimable_offer["offerSelfConnection"][
                        "eligibility"
                    ]["offerState"]
                    missing_required_account_link = not_claimable_offer[
                        "offerSelfConnection"
                    ]["eligibility"]["missingRequiredAccountLink"]
                    if offer_state == "EXPIRED":
                        log.debug(
                            "Could not claim offer! (expired) (%s from %s)",
                            game_name,
                            publisher,
                        )
                    elif missing_required_account_link:
                        log.debug(
                            "Could not claim offer! (missing required account link) (%s from %s)",
                            game_name,
                            publisher,
                        )
                    else:
                        log.debug(
                            "Could not claim offer! (unknown reason) (%s from %s)",
                            game_name,
                            publisher,
                        )

            if not claimable_offers:
                log.debug(
                    "Could not claim offers! (no claimable offers) (%s from %s)",
                    game_name,
                    publisher,
                )
                return

            if len(claimable_offers) > 1:
                log.debug(
                    "Could not claim offers! (multiple claimable offers) (%s from %s)",
                    game_name,
                    publisher,
                )
                return

            if is_fgwp:
                log.debug("It's a FGWP offer")
            else:
                log.debug("It's a ingameloot offer")
                loot_name = item["assets"]["title"]
                game_name = f"{loot_name} for {game_name}"

            log.debug("Try to claim offer %s from %s", game_name, publisher)

            claim_button = tab.query_selector(
                "button[data-a-target='buy-box_call-to-action']"
            )
            if not claim_button:
                log.debug("Claim button not found! (%s from %s)", game_name, publisher)
                return

            claim_button.click()
            log.debug("Clicked claim button %s from %s", game_name, publisher)

            if PrimeLooter.exists(tab, "div[data-a-target='LinkAccountModal']"):
                tab.click("button[data-a-target='AlreadyLinkedAccountButton']")
                log.debug(
                    "Clicked already linked account button %s from %s!",
                    game_name,
                    publisher,
                )

            tab.wait_for_load_state("networkidle")
            if grants_code:
                log.debug("Offer %s from %s grants a code!", game_name, publisher)

                tab.wait_for_selector('div[data-a-target="copy-code-input"]')

                code = (
                    tab.query_selector("div[data-a-target='copy-code-input'] input")
                    .get_attribute("value")
                    .strip()
                )
                log.debug("Code for %s (%s): %s", game_name, publisher, code)

                instructions = (
                    tab.query_selector("p[data-a-target='BodyText']")
                    .inner_text()
                    .strip()
                )
                PrimeLooter.code_to_file(game_name, code, instructions)

            if not PrimeLooter.exists(tab, 'div[class^="thank-you-title "]'):
                if self.debug:
                    with open(
                        f"./dumps/{game_name}.html", "w", encoding="utf-8"
                    ) as game_file:
                        game_file.write(
                            tab.query_selector(
                                'html[data-react-helmet="lang,dir"]'
                            ).inner_html()
                        )

                log.debug(
                    "Could not claim offer! %s (no success message) (%s from %s)",
                    tab.url,
                    game_name,
                    publisher,
                )
                return

            log.debug("Successfully claimed %s from %s!", game_name, publisher)
            return f"{game_name}"

        except Error as cex:
            log.error(str(cex))
            traceback.print_tb(cex.__traceback__)
            log.error(
                "An error occured (%s/%s)! Did they make some changes to the website? "
                "Please report @github if this happens multiple times.",
                game_name,
                publisher,
            )

        finally:
            tab.close()

    def run(self, dump: bool = False):
        """Run the script."""

        self.auth()

        if dump:
            self.page.wait_for_load_state("networkidle")
            with open("./dumps/home.html", "w", encoding="utf-8") as home_file:
                home_file.write(self.page.query_selector("div.home").inner_html())

        all_ingameloot_offers = self.get_ingameloot_offers()
        all_games_offers = self.get_games_offers()

        claimed_ingameloot_offers = [
            offer
            for offer in all_ingameloot_offers
            if PrimeLooter.check_is_claimed(offer)
        ]

        ingameloot_offers = [
            offer
            for offer in all_ingameloot_offers
            if offer not in claimed_ingameloot_offers
            and offer["assets"]["externalClaimLink"]
        ]

        not_claimable_ingameloot_offers = [
            offer
            for offer in all_ingameloot_offers
            if offer not in claimed_ingameloot_offers and offer not in ingameloot_offers
        ]

        claimed_games_offers = [
            offer for offer in all_games_offers if PrimeLooter.check_is_claimed(offer)
        ]

        games_offers = [
            offer
            for offer in all_games_offers
            if offer not in claimed_games_offers
            and offer["assets"]["externalClaimLink"]
        ]

        not_claimable_games_offers = [
            offer
            for offer in all_games_offers
            if offer not in claimed_games_offers and offer not in games_offers
        ]

        # list non claimable games offers
        if not_claimable_games_offers:
            msg = "Can not claim these games offers:"
            for offer in not_claimable_games_offers:
                msg += f"\n    - {offer['assets']['title']}"
            msg = msg[:-1]
            msg += "\n"
            log.info(msg)
        else:
            log.info("No non claimable games offers!\n")

        # list non claimable ingameloot offers
        if not_claimable_ingameloot_offers:
            msg = "Can not claim these ingameloot offers:"
            for offer in not_claimable_ingameloot_offers:
                msg += f"\n    - {offer['assets']['title']}"
            msg = msg[:-1]
            msg += "\n"
            log.info(msg)
        else:
            log.info("No non claimable ingameloot offers!\n")

        # list already claimed games offers
        if claimed_games_offers:
            msg = "The following games offers have been claimed already:"
            for offer in claimed_games_offers:
                msg += f"\n    - {offer['assets']['title']}"
            msg = msg[:-1]
            msg += "\n"
            log.info(msg)
        else:
            log.info("No claimed games offers\n")

        # list already claimed ingameloot offers
        if claimed_ingameloot_offers:
            msg = "The following ingameloot offers have been claimed already:"
            for offer in claimed_ingameloot_offers:
                msg += f"\n    - {offer['assets']['title']}"
            msg = msg[:-1]
            msg += "\n"
            log.info(msg)
        else:
            log.info("No claimed ingameloot offers\n")

        # claim games offers
        if games_offers:
            msg = "Claiming these games offers:"
            for offer in games_offers:
                msg += f"\n    - {offer['assets']['title']}"
            msg = msg[:-1]
            msg += "\n"
            log.info(msg)

            for offer in games_offers:
                self.claim(offer["assets"]["externalClaimLink"])
        else:
            log.info("No games offers to claim\n")

        # claim ingameloot offers
        if ingameloot_offers:
            msg = "Claiming these ingameloot offers:"
            for offer in ingameloot_offers:
                msg += f"\n    - {offer['assets']['title']}"
            msg = msg[:-1]
            msg += "\n"
            log.info(msg)

            for offer in ingameloot_offers:
                self.claim(offer["assets"]["externalClaimLink"])


def read_cookiefile(path: str) -> typing.List[Cookie]:
    """Read the cookies from the cookiefile."""

    jar = cookiejar.MozillaCookieJar(path)
    jar.load()

    _cookies: typing.List[Cookie] = list()

    for _c in jar:
        cookie = Cookie(
            name=_c.name,
            value=_c.value,
            domain=_c.domain,
            path=_c.path,
            expires=_c.expires,
            secure=_c.secure,
        )
        _cookies.append(cookie)
    return _cookies


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Notification bot for the lower saxony vaccination portal"
    )

    parser.add_argument(
        "-p",
        "--publishers",
        dest="publishers",
        help="Path to publishers.txt file",
        required=False,
        default="publishers.txt",
    )
    parser.add_argument(
        "-c",
        "--cookies",
        dest="cookies",
        help="Path to cookies.txt file",
        required=False,
        default="cookies.txt",
    )
    parser.add_argument(
        "-l",
        "--loop",
        dest="loop",
        help="Shall the script loop itself? (Cooldown 24h)",
        required=False,
        action="store_true",
        default=False,
    )

    parser.add_argument(
        "--dump",
        dest="dump",
        help="Dump html to output",
        required=False,
        action="store_true",
        default=False,
    )

    parser.add_argument(
        "-d",
        "--debug",
        dest="debug",
        help="Print Log at debug level",
        required=False,
        action="store_true",
        default=False,
    )

    parser.add_argument(
        "-nh",
        "--no-headless",
        dest="headless",
        help="Shall the script not use headless mode?",
        required=False,
        action="store_false",
        default=True,
    )

    arg = vars(parser.parse_args())

    cookies_arg = read_cookiefile(arg["cookies"])

    with open(arg["publishers"], encoding="utf-8") as f:
        publishers_temp = f.readlines()
    publishers_arg = [x.strip() for x in publishers_temp]
    headless_arg = arg["headless"]
    dump_arg = arg["dump"]
    debug_arg = arg["debug"]

    if debug_arg:
        log.level = logging.DEBUG

    with PrimeLooter(
        cookies_arg,
        publishers_arg,
        headless_arg,
        debug_arg,
        use_chrome=False,
    ) as looter:
        while True:
            try:
                log.info("Starting Prime Looter\n")
                looter.run(dump_arg)
                log.info("Finished Looting!\n")
            except AuthException as ex:
                log.error(ex)
                sys.exit(1)
            except (ClaimException, Exception) as ex:
                log.error(ex)
                traceback.print_tb(ex.__traceback__)
                time.sleep(60)
            else:
                if arg["loop"]:
                    handler.terminator = "\r"

                    SLEEP_TIME = 60 * 60 * 24
                    for time_slept in range(SLEEP_TIME):
                        m, s = divmod(SLEEP_TIME - time_slept, 60)
                        h, m = divmod(m, 60)
                        log.info("%d:%02d:%02d till next run...", h, m, s)
                        time.sleep(1)

                    handler.terminator = "\n"

            if not arg["loop"]:
                break
