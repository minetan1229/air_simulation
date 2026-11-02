def main() -> None:
    with open('physics/records_domain.py', 'r', encoding='utf-8') as f:
        text = f.read()

    print('Count of release_pitch_deg: 0.0 ->', text.count('"release_pitch_deg": 0.0'))
    print('Count of U+FFFD ->', text.count('\ufffd'))


if __name__ == "__main__":
    main()
