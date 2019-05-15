CREATE TABLE IF NOT EXISTS `reviews` (
	`id`	INTEGER PRIMARY KEY AUTOINCREMENT,
	`status`	TEXT,
	`repo_id`	INTEGER,
    `pr_url`   INTEGER,
	`base_commit_sha` TEXT,
    `head_commit_sha` TEXT
);
CREATE TABLE IF NOT EXISTS `repositories` (
	`id`	INTEGER PRIMARY KEY AUTOINCREMENT,
	`user`	TEXT,
	`repo`	TEXT,
	`status`	TEXT
);
CREATE TABLE IF NOT EXISTS `modifiedfiles` (
    `id`    INTEGER PRIMARY KEY AUTOINCREMENT,
    `review_id` INTEGER,
    `old_filename` TEXT,
    `new_filename` TEXT
);
CREATE TABLE IF NOT EXISTS `methodcalls` (
    `id`	INTEGER PRIMARY KEY AUTOINCREMENT,
    `review_id` TEXT,
    `from_file` TEXT,
    `call_start_line` INTEGER,
    `call_start_column` INTEGER,
    `call_end_line` INTEGER,
    `call_end_column` INTEGER,
    `method_call` TEXT,
    `short_method_qualifier` TEXT,
    `full_method_qualifier` TEXT,
    `to_file` TEXT,
    `declaration_start_line` INTEGER,
    `declaration_start_column` INTEGER,
    `declaration_end_line` INTEGER,
    `declaration_end_column` INTEGER
);
