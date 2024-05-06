import yaml
import os
from warnings import warn

from concurrent.futures import ThreadPoolExecutor, as_completed


def update_available_repositories(name, data):
    print(f"Updating {name}...")

    # update versions
    if "pip" in data:
        results = os.popen(f"pip index versions {data['pip']}").read().split("\n")

    versions = []
    for l in results:
        if l.startswith("Available versions: "):
            versions = l.split(": ")[1].split(", ")

    if len(versions) == 0:
        warn(f"Failed to get versions for {name}")
    else:
        data["versions"] = versions


def update_all_repositories(repositories):
    # Use a thread pool to run updates in parallel
    with ThreadPoolExecutor() as executor:
        # Start the update tasks
        futures = {
            executor.submit(update_available_repositories, name, data): name
            for name, data in repositories.items()
        }

        # Wait for all tasks to complete
        for future in as_completed(futures):
            name = futures[future]
            try:
                future.result()  # Check for exceptions
            except Exception as exc:
                print(f"{name} generated an exception: {exc}")


if __name__ == "__main__":
    # change working directory to the script directory
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    with open("available_repositories.yaml") as file:
        available_repositories = yaml.load(file, Loader=yaml.FullLoader)

    # Run updates in parallel
    update_all_repositories(available_repositories)

    with open("available_repositories.yaml", "w") as file:
        yaml.dump(available_repositories, file)

    # make README.md
    with open("README.md", "w") as file:
        file.write(
            "## Overview collection of funcnode repositories\n\n"
            "This is a list of repositories that are available for funcnodes.\n\n"
            "### Current repositories:\n\n"
        )

        for name, data in available_repositories.items():
            title = name
            if data["versions"]:
                title += f" ({data['versions'][0]})"
            if data["repository"]:
                title = f"[{title}]({data['repository']})"
            file.write(f"#### {title}\n\n")
            file.write(f"{data['description']}\n\n")
            file.write("\n")
