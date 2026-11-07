                                    #This Database is used with Datagrip (MySql)
create database GDC_Bank;
use GDC_Bank;
create table customers(
    id integer primary key,
    full_name varchar(40) not null ,
    email varchar(60) unique ,
    created_at date not null
);
CREATE TABLE accounts (
    id INT PRIMARY KEY AUTO_INCREMENT,
    id_customers INT NOT NULL,
    balance DECIMAL(15, 2) NOT NULL DEFAULT 0.00,
    created_at DATE NOT NULL,
    FOREIGN KEY (id_customers) REFERENCES customers(id)
);
CREATE TABLE transactions (
    id INT PRIMARY KEY AUTO_INCREMENT,
    account_id INT NOT NULL,
    type VARCHAR(20) NOT NULL,
    amount DECIMAL(15,2) NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (account_id) REFERENCES accounts(id)
);
alter table accounts
RENAME COLUMN id_customers TO customers_id;

alter table customers
MODIFY COLUMN id INT AUTO_INCREMENT;
ALTER TABLE accounts
DROP FOREIGN KEY accounts_ibfk_1;
ALTER TABLE accounts
ADD CONSTRAINT  fk_account_customers
FOREIGN KEY (customers_id) REFERENCES  customers(id);
ALTER TABLE customers    ADD CONSTRAINT chk_email   CHECK (email LIKE '_%@_%._%');
ALTER TABLE customers    ADD CONSTRAINT chk_name    CHECK (CHAR_LENGTH(TRIM(full_name)) >= 2);
ALTER TABLE accounts     ADD CONSTRAINT chk_balance CHECK (balance >= 0);
ALTER TABLE transactions ADD CONSTRAINT chk_amount  CHECK (amount > 0);
ALTER TABLE transactions ADD CONSTRAINT chk_type    CHECK (type IN ('deposit','withdrawal','transf_out','transf_in'));
