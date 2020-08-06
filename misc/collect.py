#!/usr/bin/env python3
"""
Tool to create overview.json files and update the config.js.
"""

from pathlib import Path
import urllib.request
import tempfile
import argparse
import json
import glob
import sys
import os
import re

SUPPORTED_METADATA_VERSION = 1


# accepts {<file-path>: <file-content>}
def merge_profiles(profiles, download_url):
    # json output data
    output = {}

    def get_title(title):
        if "title" in title:
            return title["title"]
        else:
            return "{} {} {}".format(
                title.get("vendor", ""), title["model"], title.get("variant", "")
            ).strip()

    def add_profile(id, target, profile, code=None):
        images = []
        for image in profile["images"]:
            images.append({"name": image["name"], "type": image["type"]})

        if target is None:
            target = profile["target"]

        for entry in profile["titles"]:
            title = get_title(entry)

            if len(title) == 0:
                sys.stderr.write(f"Empty title. Skip title in {path}\n")
                continue

            output["models"][title] = {"id": id, "target": target, "images": images}

            if code is not None:
                output["models"][title]["code"] = code

    for path, content in profiles.items():
        obj = json.loads(content)

        if obj["metadata_version"] != SUPPORTED_METADATA_VERSION:
            sys.stderr.write(
                f"{path} has unsupported metadata version: {obj['metadata_version']} => skip\n"
            )
            continue

        code = obj.get("version_code", obj.get("version_commit"))

        if "version_code" not in output:
            output = {"version_code": code, "download_url": download_url, "models": {}}

        # if we have mixed codes/commits, store in device object
        if output["version_code"] == code:
            code = None

        try:
            if "profiles" in obj:
                for id in obj["profiles"]:
                    add_profile(id, obj.get("target"), obj["profiles"][id], code)
            else:
                add_profile(obj["id"], obj["target"], obj, code)
        except json.decoder.JSONDecodeError as e:
            sys.stderr.write(f"Skip {path}\n   {e}\n")
        except KeyError as e:
            sys.stderr.write(f"Abort on {path}\n   Missing key {e}\n")
            exit(1)

    return output


def update_config(config_path, versions):
    content = ""
    with open(config_path, "r") as file:
        content = file.read()

    content = re.sub("versions:[\\s]*{[^}]*}", f"versions: {versions}", content)
    with open(config_path, "w+") as file:
        file.write(content)


"""
Scrape profiles.json using links like https://downloads.openwrt.org/releases/19.07.3/targets/?json
Merge into overview.json files.
Update config.json.
"""


def scrape(args):
    url = args.domain
    selector_path = args.selector
    config_path = f"{selector_path}/config.js"
    data_path = f"{selector_path}/data"
    versions = {}

    def handle_release(target):
        profiles = {}
        with urllib.request.urlopen(f"{target}/?json") as file:
            array = json.loads(file.read().decode("utf-8"))
            for profile in filter(lambda x: x.endswith("/profiles.json"), array):
                with urllib.request.urlopen(f"{target}/{profile}") as file:
                    profiles[f"{target}/{profile}"] = file.read()
        return profiles

    if not os.path.isfile(config_path):
        print(f"file not found: {config_path}")
        exit(1)

    # fetch release URLs
    with urllib.request.urlopen(url) as infile:
        for path in re.findall(r"href=[\"']?([^'\" >]+)", str(infile.read())):
            if not path.startswith("/") and path.endswith("targets/"):
                release = path.strip("/").split("/")[-2]
                download_url = f"{url}/{path}/{{target}}"

                profiles = handle_release(f"{url}/{path}")
                output = merge_profiles(profiles, download_url)
                if len(output) > 0:
                    Path(f"{data_path}/{release}").mkdir(parents=True, exist_ok=True)
                    # write overview.json
                    with open(f"{data_path}/{release}/overview.json", "w") as outfile:
                        if args.formatted:
                            json.dump(output, outfile, indent="  ", sort_keys=True)
                        else:
                            json.dump(output, outfile, sort_keys=True)

                    versions[release.upper()] = f"data/{release}/overview.json"

    update_config(config_path, versions)


"""
Scrape profiles.json using wget (slower but more generic).
Merge into overview.json files.
Update config.json.
"""


def scrape_wget(args):
    url = args.domain
    selector_path = args.selector
    config_path = f"{selector_path}/config.js"
    data_path = f"{selector_path}/data"
    versions = {}

    with tempfile.TemporaryDirectory() as tmp_dir:
        # download all profiles.json files
        os.system(
            f"wget -c -r -P {tmp_dir} -A 'profiles.json' --reject-regex 'kmods|packages' --no-parent {url}"
        )

        # delete empty folders
        os.system(f"find {tmp_dir}/* -type d -empty -delete")

        # create overview.json files
        for path in glob.glob(f"{tmp_dir}/*/snapshots") + glob.glob(
            f"{tmp_dir}/*/releases/*"
        ):
            release = os.path.basename(path)
            base = path[len(tmp_dir) + 1 :]

            versions[release.upper()] = f"data/{release}/overview.json"
            os.system(f"mkdir -p {selector_path}/data/{release}/")

            profiles = {}
            for ppath in Path(path).rglob("profiles.json"):
                with open(ppath, "r") as file:
                    profiles[ppath] = file.read()

            output = merge_profiles(profiles, f"https://{base}/targets/{{target}}")
            Path(f"{data_path}/{release}").mkdir(parents=True, exist_ok=True)

            # write overview.json
            with open(f"{data_path}/{release}/overview.json", "w") as outfile:
                if args.formatted:
                    json.dump(output, outfile, indent="  ", sort_keys=True)
                else:
                    json.dump(output, outfile, sort_keys=True)

        update_config(config_path, versions)


"""
Find and merge json files for a single release.
"""


def merge(args):
    input_paths = args.input_path
    # OpenWrt JSON device files
    profiles = {}

    def add_path(path):
        with open(path, "r") as file:
            profiles[path] = file.read()

    for path in input_paths:
        if os.path.isdir(path):
            for filepath in Path(path).rglob("*.json"):
                add_path(filepath)
        else:
            if not path.endswith(".json"):
                sys.stderr.write(f"Folder does not exists: {path}\n")
                exit(1)
            add_path(path)

    output = merge_profiles(profiles, args.download_url)

    if args.formatted:
        json.dump(output, sys.stdout, indent="  ", sort_keys=True)
    else:
        json.dump(output, sys.stdout, sort_keys=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--formatted", action="store_true", help="Output formatted JSON data."
    )
    subparsers = parser.add_subparsers(dest="action", required=True)

    parser_merge = subparsers.add_parser(
        "merge",
        help="Create a grid structure with horizontal and vertical connections.",
    )
    parser_merge.add_argument(
        "input_path",
        nargs="+",
        help="Input folder that is traversed for OpenWrt JSON device files.",
    )
    parser_merge.add_argument(
        "--download-url",
        action="store",
        default="",
        help="Link to get the image from. May contain {target}, {version} and {commit}",
    )

    parser_scrape = subparsers.add_parser(
        "scrape",
        help="Create a grid structure of horizontal, vertical and vertical connections.",
    )
    parser_scrape.add_argument(
        "domain", help="Domain to scrape. E.g. https://downloads.openwrt.org"
    )
    parser_scrape.add_argument("selector", help="Path the config.js file is in.")
    parser_scrape.add_argument(
        "--use-wget", action="store_true", help="Use wget to scrape the site."
    )

    args = parser.parse_args()

    if args.action == "merge":
        merge(args)

    if args.action == "scrape":
        if args.use_wget:
            scrape_wget(args)
        else:
            scrape(args)


if __name__ == "__main__":
    main()
