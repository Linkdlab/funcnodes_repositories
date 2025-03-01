import requests
import bs4
import os
import subprocess
import tarfile
import zipfile
import configparser
from io import StringIO
import json
import pandas as pd
import time
import tqdm
from packaging.version import parse as parse_version
import argparse


def download_package(pypi_info, download_dir="packages"):
    """Download the package from PyPI."""

    os.makedirs(download_dir, exist_ok=True)

    url = pypi_info["urls"][0]["url"]
    filename = pypi_info["urls"][0]["filename"]
    trg = os.path.join(download_dir, filename)
    if os.path.exists(trg):
        return trg
    with open(trg, "wb") as f:
        response = requests.get(url)
        f.write(response.content)

    return trg


def extract_package(package_path, extract_dir="extracted"):
    """Extract the package files."""
    os.makedirs(extract_dir, exist_ok=True)
    if package_path.endswith(".tar.gz"):
        with tarfile.open(package_path, "r:gz") as tar:
            tar.extractall(path=extract_dir)
    elif package_path.endswith(".whl"):
        with zipfile.ZipFile(package_path, "r") as zip_ref:
            zip_ref.extractall(extract_dir)
    return extract_dir


def find_entry_points(extract_dir):
    """Find and parse entry points from setup.cfg or setup.py."""
    entry_points = {}
    for root, _, files in os.walk(extract_dir):
        if "setup.cfg" in files:
            config = configparser.ConfigParser()
            config.read(os.path.join(root, "setup.cfg"))
            if config.has_section("options.entry_points"):
                entry_points = config["options.entry_points"]
        elif "setup.py" in files:
            with open(os.path.join(root, "setup.py"), "r") as f:
                content = f.read()
                if "entry_points" in content:
                    exec_globals = {}
                    exec(content, exec_globals)
                    entry_points = exec_globals.get("entry_points", {})
        elif "entry_points.txt" in files:
            with open(os.path.join(root, "entry_points.txt"), "r") as f:
                txt = f.read()
            if "[funcnodes.module]" in txt:
                in_block = False
                for line in txt.split("\n"):
                    if line.startswith("[funcnodes.module]"):
                        in_block = True
                        continue
                    if in_block:
                        if "=" in line:
                            key, value = line.split("=", 1)
                            entry_points[key.strip()] = value.strip()
                        else:
                            in_block = False

        if entry_points:
            break
    return entry_points


def get_pipy_info(package_name):
    url = f"https://pypi.org/pypi/{package_name}/json"
    response = requests.get(url)
    return response.json()


def get_package_info(package_name):
    pypi_info = get_pipy_info(package_name)
    version = pypi_info["info"]["version"]
    tmpdir = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "tmp", package_name, version
    )
    if not os.path.exists(tmpdir):
        os.makedirs(tmpdir)
    with open(os.path.join(tmpdir, "pypi.json"), "w+") as f:
        json.dump(pypi_info, f, indent=2)

    description = pypi_info["info"]["description"]

    package_path = download_package(pypi_info, os.path.join(tmpdir, "dl"))
    extract_dir = extract_package(package_path, os.path.join(tmpdir, "ex"))

    entry_points = find_entry_points(extract_dir)
    entry_points = {f"entry_point__{k}": v for k, v in entry_points.items()}

    urls = {}
    for k, v in (pypi_info["info"].get("project_urls") or {}).items():
        if k in ["Homepage", "homepage"]:
            urls["homepage"] = v
            continue
        if k in ["source", "Source"]:
            urls["source"] = v
            continue

    # escape \n\t and all other special characters
    description = description.encode("unicode_escape").decode("utf-8")
    sorted_releases = sorted(
        pypi_info["releases"].keys(), key=parse_version, reverse=True
    )
    datadict = {
        "package_name": package_name,
        "version": version,
        "description": description,
        "summary": pypi_info["info"]["summary"],
        "releases": ", ".join(sorted_releases),
        **urls,
    }
    datadict.update(entry_points)
    return datadict


def search_pypi():
    page = 1
    packages = []
    url = "https://pypi.org/simple/"
    text = b""
    with requests.get(
        url,
        stream=True,
        headers={"Accept": "application/vnd.pypi.simple.v1+json"},
        timeout=600,
    ) as response:
        response.raise_for_status()
        with tqdm.tqdm(
            unit="B", unit_scale=True, unit_divisor=1024, desc="getting PyPi Index"
        ) as pbar:
            for chunk in response.iter_content(chunk_size=8192):
                text += chunk
                pbar.update(len(chunk))

    jsondata = json.loads(text)["projects"]
    funcnodes_packages = [
        p["name"] for p in jsondata if "funcnodes" in p["name"].lower()
    ]
    return funcnodes_packages
    # while True:
    #     print(f"Searching page {page}...")
    #     url = f"https://pypi.org/search?q=funcnodes&page={page}"

    #     response = requests.get(url)
    #     print(response.text)
    #     soup = bs4.BeautifulSoup(response.text, "html.parser")
    #     # get main tag
    #     main = soup.find("main")
    #     if main is None:
    #         break

    #     # get form tag with action="/search/"
    #     form = main.find("form", action="/search/")

    #     if form is None:
    #         break

    #     # get all links with href="/project/*/"
    #     for a in form.find_all("a", href=True):
    #         if a["href"].startswith("/project/"):
    #             packages.append(a["href"][9:-1])
    #     page += 1

    # return packages


def main():
    argparser = argparse.ArgumentParser()
    argparser.add_argument(
        "--no-simple", action="store_true", help="Don't use the simple index"
    )

    args = argparser.parse_args()

    if os.path.exists("funcnodes_modules.csv"):
        df = pd.read_csv("funcnodes_modules.csv")

        for col in ["package_name", "last_updated"]:
            if col not in df.columns:
                df[col] = None
        df.index = df["package_name"]
        df = df.replace({float("nan"): None})
    else:
        df = pd.DataFrame()

    if args.no_simple:
        packages = []
    else:
        packages = search_pypi()
    with open(os.path.join(os.path.dirname(__file__), "official.txt"), "r") as f:
        official = f.read().split("\n")

    for pw in official:
        if pw not in packages:
            packages.append(pw)

    with open(os.path.join(os.path.dirname(__file__), "blacklist.txt"), "r") as f:
        blacklist = f.read().split("\n")
    packages = [p for p in packages if p not in blacklist]

    added = []
    updated = []
    unchanged = []

    package_infos = [
        get_package_info(package)
        for package in tqdm.tqdm(
            packages, desc="Get Infos packages", total=len(packages)
        )
    ]

    series = []
    now = pd.Timestamp.now()
    for package_info in tqdm.tqdm(
        package_infos, desc="Making series packages", total=len(package_infos)
    ):
        name = package_info["package_name"]
        if name in official:
            package_info["official"] = True
        else:
            package_info["official"] = False
        if name is None:
            raise ValueError("Package name is None")
        if "summary" not in package_info or not package_info["summary"]:
            if "description" in package_info:
                package_info["summary"] = package_info["description"]
            else:
                package_info["summary"] = ""
        if len(package_info["summary"]) > 400:
            package_info["summary"] = package_info["summary"][:400] + "..."

        ser = pd.Series(package_info, name=name)
        series.append(ser)

    for ser in tqdm.tqdm(series, desc="Processing series", total=len(series)):
        for col in ser.index:
            if col not in df.columns:
                df[col] = None
        if ser.name in df.index:
            changed = False
            for col in ser.index:
                if ser[col] == "":
                    ser[col] = None
                if df.loc[ser.name, col] != ser[col]:
                    changed = True
                    print(
                        f"{ser.name}: {col} changed from '{df.loc[ser.name, col]}' to '{ser[col]}'"
                    )
                    df.loc[ser.name, col] = ser[col]
            if changed:
                df.loc[ser.name, "last_updated"] = now
                updated.append(ser.name)
            else:
                unchanged.append(ser.name)
        else:
            ser["last_updated"] = now
            df = pd.concat([df, ser.to_frame().T])  # Add new entry
            added.append(ser.name)

    df = df.replace({float("nan"): None})
    print("unchanged:", unchanged, "\ntotal:", len(unchanged))
    print("updated:", updated, "\ntotal:", len(updated))
    print("added:", added, "\ntotal:", len(added))

    df.to_csv("funcnodes_modules.csv", index=False)

    with open("README_template.md", "r") as f:
        template = f.read()

    for row, rowdata in df.iterrows():
        template += f"#### [{rowdata['package_name']} ({rowdata['version']})]({rowdata['source'] or rowdata['homepage'] or ''})\n\n"
        summary = rowdata["summary"] or ""
        # summary might contain literal "\n" and "\t" characters which should be replaced with actual newlines and tabs
        if summary:
            summary = summary.replace("\\n", "\n").replace("\\t", "\t")
        template += f"{summary}\n\n\n"

    with open("README.md", "w") as f:
        f.write(template)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(e)
