from sqlalchemy.ext.asyncio import session
from werkzeug.security import generate_password_hash,check_password_hash

from flask import Flask, render_template, request, redirect
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import pymysql


pymysql.install_as_MySQLdb()

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI']='mysql+pymysql://ryan:password123@localhost/todo_db'
db=SQLAlchemy(app)

class User(db.Model):
    id=db.Column(db.Integer, primary_key=True)
    username=db.Column(db.String(50), unique=True, nullable=False)
    password_hash=db.Column(db.String(255), nullable=False)

    def __repr__(self):
        return f'<User {self.username}>'

class Todo(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    content= db.Column(db.String(200), nullable=False)
    date_created=db.Column(db.DateTime, default=datetime.utcnow)
    completed= db.Column(db.Boolean, default=False)
    def __repr__(self):
        return f'<Task {self.id}>'


@app.route('/register', methods=['POST','GET'])
def register():
    if request.method=='POST':
        username=request.form['username']
        password=request.form['password']
        if not username.strip() and password.strip():
            password_hash=generate_password_hash(password)

            user=User(username=username,password_hash=password_hash)
            try:
                db.session.add(user)
                db.session.commit()
                return redirect('/login')
            except Exception as e:
                return str(e)
    return render_template('register.html')

@app.route('/login', methods=['POST', 'GET'])
def login():
    if request.method == 'POST':
        username=request.form['username']
        password=request.form['password']
        user=User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password_hash, password):
            session['user_id']=user.id
            return redirect('/')
        return 'Invalid password or username'
    return render_template('login.html')


@app.route('/', methods=['POST','GET'])
def home():
    if request.method=='POST':
        task_content=request.form['content']
        if task_content.strip():
            new_task=Todo(content= task_content)
            try:
                db.session.add(new_task)
                db.session.commit()
                return redirect('/')
            except:
                return 'There was an issue adding your task'
        else:
            return redirect('/')
    else:
        tasks=Todo.query.order_by(Todo.date_created).all()
        return render_template('index.html', tasks=tasks)

@app.route('/delete/<int:id>')
def delete(id):
    task_to_delete=Todo.query.get_or_404(id)
    try:
        db.session.delete(task_to_delete)
        db.session.commit()
        return redirect('/')
    except:
        return 'We are facing a problem'

@app.route('/update/<int:id>', methods=['GET','POST'])
def update(id):
    task_to_update=Todo.query.get_or_404(id)
    if request.method =='POST':
        task_to_update.content=request.form['content']
        try:
            db.session.commit()
            return redirect('/')
        except:
            return 'There was an issue'
        return render_template('update.html', task=task_to_update)
@app.route('/complete/<int:id>', methods=['GET'])
def complete(id):
    task_to_update= Todo.query.get_or_404(id)
    try:
        task_to_update.completed= True
        db.session.commit()
        return redirect('/')
    except:
        return 'There was a problem completing the task'



if __name__ == '__main__':
    with app.app_context():
        db.create_all()

    app.run(debug=True)
