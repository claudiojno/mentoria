CREATE DATABASE IF NOT EXISTS demo;
USE demo;

DROP TABLE IF EXISTS customers;

CREATE TABLE customers (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(120) NOT NULL,
  email VARCHAR(120) NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO customers (name, email) VALUES
('Ana Souza', 'ana@exemplo.com'),
('Bruno Lima', 'bruno@exemplo.com'),
('Carla Pereira', 'carla@exemplo.com');
