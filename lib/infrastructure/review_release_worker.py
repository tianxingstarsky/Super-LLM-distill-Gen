"""Fixed entry point for local review publication workers."""
import argparse

from lib.infrastructure.review_release_jobs import execute_release_job


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--run", required=True)
    parser.add_argument("--target", required=True, choices=("sft", "cpt", "dpo", "orpo"))
    parser.add_argument("--job", required=True)
    args = parser.parse_args()
    execute_release_job(args.output, args.run, args.target, args.job)


if __name__ == "__main__":
    main()
