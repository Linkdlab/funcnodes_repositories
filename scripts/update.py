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
    tmpdir = os.path.join(os.path.dirname(__file__), "tmp", package_name)
    if not os.path.exists(tmpdir):
        os.makedirs(tmpdir)
    pypi_info = get_pipy_info(package_name)
    with open(os.path.join(tmpdir, "pypi.json"), "w+") as f:
        json.dump(pypi_info, f, indent=2)

    version = pypi_info["info"]["version"]
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
    datadict = {
        "package_name": package_name,
        "version": version,
        "description": description,
        "summary": pypi_info["info"]["summary"],
        **urls,
    }
    datadict.update(entry_points)
    return datadict


def search_pypi():
    page = 1
    packages = []
    while True:
        print(f"Searching page {page}...")
        url = f"https://pypi.org/search?q=funcnodes&page={page}"

        response = requests.get(url)
        soup = bs4.BeautifulSoup(response.text, "html.parser")
        # get main tag
        main = soup.find("main")
        if main is None:
            break

        # get form tag with action="/search/"
        form = main.find("form", action="/search/")

        if form is None:
            break

        # get all links with href="/project/*/"
        for a in form.find_all("a", href=True):
            if a["href"].startswith("/project/"):
                packages.append(a["href"][9:-1])
        page += 1

    return packages


def main():
    if os.path.exists("funcnodes_modules.csv"):
        df = pd.read_csv("funcnodes_modules.csv")

        for col in ["package_name", "last_updated"]:
            if col not in df.columns:
                df[col] = None
        df.index = df["package_name"]
    else:
        df = pd.DataFrame()
    packages = search_pypi()
    with open(os.path.join(os.path.dirname(__file__), "blacklist.txt"), "r") as f:
        blacklist = f.read().split("\n")
    packages = [p for p in packages if p not in blacklist]

    for package in packages:
        package_info = get_package_info(package)
        print(package_info["package_name"])
        _df = pd.DataFrame([package_info], index=[package_info["package_name"]])
        _df["last_updated"] = pd.Timestamp.now()

        if not _df.loc[package_info["package_name"], "summary"]:
            _df.loc[package_info["package_name"], "summary"] = _df.loc[
                package_info["package_name"], "description"
            ]

            if len(_df.loc[package_info["package_name"], "summary"]) > 400:
                _df.loc[package_info["package_name"], "summary"] = (
                    _df.loc[package_info["package_name"], "summary"][:400] + "..."
                )

        for col in _df.columns:
            if col not in df.columns:
                df[col] = None

        if package_info["package_name"] in df.index:
            print("Updating", flush=True)
            df.loc[package_info["package_name"]] = _df.loc[package_info["package_name"]]
        else:
            print("Appending", flush=True)
            df = pd.concat([df, _df])
    df = df.replace({float("nan"): None})
    print("AAAs")

    df.to_csv("funcnodes_modules.csv", index=False)

    with open("README_template.md", "r") as f:
        template = f.read()

    for row, rowdata in df.iterrows():
        template += f"#### [{rowdata['package_name']} ({rowdata['version']})]({rowdata['source'] or rowdata['homepage'] or ''})\n\n"
        template += f"{rowdata['summary'] if rowdata['summary'] else ''}\n\n\n"

    with open("README.md", "w") as f:
        f.write(template)


if __name__ == "__main__":
    main()
